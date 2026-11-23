"""Build facility-level residue supply on the v3 province-capacity-floor case.

The builder joins two upstream screening artifacts:

* ``national_product_chain_v1`` supplies provincial class feedstock, product,
  process residue, and province-level harvest residue.
* ``national_facility_assignment_v3`` supplies the ``province_capacity_floor``
  facility set and the assigned volume used as the allocation weight.

The join is class-safe.  A SAW facility receives only SAW feedstock, a PULP
facility only PULP feedstock, and so on.  Within an origin province and class,
feedstock is allocated in proportion to the facility's assigned volume in
assignment v3.  When a province has no assigned facility of the required class,
the class stream is routed to the national set of assigned facilities of that
class; the fallback is explicit in ``allocation_scope``.

Residue is deliberately split into two streams:

* **process residue** is generated at a facility from its class feedstock and
  is reported per facility/province in odt and PJ;
* **harvest residue** is a provincial estimate (gross, recoverable,
  unrecovered) and is never assigned to a facility.

The RSYC comparison uses recoverable *harvest* residue only.  Process residue
is reported alongside it and is never added to the RSYC comparison without an
explicit combined label.

Fail-closed rules
-----------------
* No NaN, infinity, or negative quantity is written.
* Every facility allocation is class-matched.
* Facility class feedstock, products, and process residue must sum to the
  product-chain province/class totals.
* Harvest residue must reconcile to the product chain and stay separate from
  facility process residue.
* Any unexpected ``D`` in the parent or ``data`` repository status halts the
  builder.  The builder never deletes files.

Output labels
-------------
Every row is ``model_estimate=true``, ``observed=false``, and
``policy_grade=false``.  The artifact is a screening supply integration, not an
operating, policy, AAC, tenure, or investment claim.  No UI or public OSeCAN
surface is changed.
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
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v1"
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v1"
)
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v1"
PRODUCT_CHAIN_LABEL = "national_product_chain_v1"
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v3"
)
RSYC_SUMMARY = (
    DATA_ROOT
    / "derived/national/clews_supply/rsyc_backbone/"
    "national_residue_factor_summary.json"
)

ARTIFACT = "facility_residue_supply_v1"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v1"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V1.md"
ASSIGNMENT_VERSION = "v3"
ASSIGNMENT_LABEL = "assignment v3"
ASSIGNMENT_ARTIFACT = "national_facility_assignment_v3"
ASSIGNMENT_SUMMARY_KEY = "assignment_v3"
ALLOCATION_BASIS = "assignment_v3_province_capacity_floor_assigned_m3"
UTILIZATION_FIELD = "assignment_utilization_v3"
UTILIZATION_MODE = "row"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply"
TEST_PATH = "tests/test_facility_residue_supply.py"
SCENARIO = "province_capacity_floor"
PJ_GJ = 1_000_000.0

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
CLASS_FEEDSTOCK_COLUMN = {
    "SAW": "sawlog_feedstock_m3",
    "PULP": "pulp_feedstock_m3",
    "PANEL": "panel_feedstock_m3",
    "PELLET": "pellet_feedstock_m3",
    "BIO": "bio_feedstock_m3",
}
CLASS_PRODUCT_COLUMN = {
    "SAW": "sawlog_product_m3",
    "PULP": "pulp_product_odt",
    "PANEL": "panel_product_m3",
    "PELLET": "pellet_product_odt",
    "BIO": "bio_product_mwh",
}
CLASS_RESIDUE_COLUMN = {
    "SAW": "sawlog_residue_odt",
    "PULP": "pulp_residue_odt",
    "PANEL": "panel_residue_odt",
    "PELLET": "pellet_residue_odt",
    "BIO": "bio_residue_odt",
}
CLASS_PRODUCT_UNIT = {
    "SAW": "m3",
    "PULP": "odt",
    "PANEL": "m3",
    "PELLET": "odt",
    "BIO": "MWh",
}
PUBLISHED = "published"
PROXY = "proxy"
SCALED_PROXY = "proxy_harvest_scaled"
CAPACITY_STATUSES = {PUBLISHED, PROXY, SCALED_PROXY}
CAPACITY_EVIDENCE = {
    PUBLISHED: "published_mills_db_2015_infeed_proxy",
    PROXY: "class_median_proxy",
    SCALED_PROXY: "class_median_proxy_scaled_to_harvest_floor",
}
CAPACITY_EVIDENCE_ALLOWED = {
    status: frozenset({evidence})
    for status, evidence in CAPACITY_EVIDENCE.items()
}
RESIDUE_DENSITY_ODT_M3 = 0.45
RSYC_BASE_RECOVERY = 0.60
REL_TOL = 1e-8
ABS_TOL = 1e-6
LABEL_TRUE = "true"
LABEL_FALSE = "false"
EVIDENCE_LABEL = "model_estimate"
RSYC_100M_HARVEST_M3 = 100_000_000.0


class FacilityResidueSupplyError(RuntimeError):
    """Raised when the facility residue supply integration fails a guard."""


@dataclass(frozen=True)
class RsvcBasis:
    """Documented RSYC energy and reference values."""

    lhv_gj_per_odt: float
    lhv_source: str
    base_100m_recoverable_odt: float
    base_100m_recoverable_pj: float


@dataclass(frozen=True)
class ProvinceChain:
    """Parsed product-chain province row in trusted internal form."""

    province: str
    province_name: str
    harvest_m3: float
    class_feedstock_m3: Mapping[str, float]
    class_product: Mapping[str, float]
    class_residue_odt: Mapping[str, float]
    process_residue_odt: float
    harvest_residue_gross_m3: float
    harvest_residue_gross_odt: float
    harvest_residue_recoverable_m3: float
    harvest_residue_recoverable_odt: float
    harvest_residue_unrecovered_m3: float
    harvest_residue_unrecovered_odt: float
    rsyc_base_recoverable_odt: float
    model_recoverable_odt_base: float
    rsyc_ratio: float
    rsyc_status: str
    coefficient_tier: str
    coefficient_source: str


@dataclass(frozen=True)
class AssignmentLink:
    """One assignment-v3 row: an origin province offered volume to a facility."""

    origin_province: str
    facility_id: str
    facility_name: str
    facility_class: str
    facility_province: str
    cross_border: bool
    capacity_status: str
    capacity_evidence: str
    floor_scale_factor: float
    capacity_m3_y: float
    assigned_m3: float
    utilization: float
    evidence_tier: str


@dataclass(frozen=True)
class ResidueAllocation:
    """Validated class-matched feedstock, product, and residue at one facility."""

    origin_province: str
    facility_id: str
    facility_name: str
    facility_class: str
    feedstock_class: str
    facility_province: str
    cross_border: bool
    allocation_scope: str
    assignment_weight_m3: float
    assignment_share: float
    capacity_status: str
    capacity_evidence: str
    floor_scale_factor: float
    capacity_m3_y: float
    assigned_m3: float
    utilization: float
    feedstock_m3: float
    product_quantity: float
    product_unit: str
    product_per_m3: float
    residue_odt: float
    residue_pj: float
    residue_odt_per_m3: float
    coefficient_tier: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path, required: Sequence[str], label: str) -> pd.DataFrame:
    if not path.is_file():
        raise FacilityResidueSupplyError(f"{label} does not exist: {path}")
    frame = pd.read_csv(path, keep_default_na=False, dtype=str)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise FacilityResidueSupplyError(
            f"{label} is missing columns {missing}: {path}"
        )
    if frame.empty:
        raise FacilityResidueSupplyError(f"{label} is empty: {path}")
    return frame


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FacilityResidueSupplyError(
            f"{label} is not numeric: {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise FacilityResidueSupplyError(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0:
        raise FacilityResidueSupplyError(f"{label} is negative: {parsed!r}")
    return parsed


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise FacilityResidueSupplyError(f"{label} is not a boolean: {value!r}")


def _require_close(left: float, right: float, label: str) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > REL_TOL * scale:
        raise FacilityResidueSupplyError(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(relative {abs(left - right) / scale:.3e})"
        )


def _require_between(value: float, low: float, high: float, label: str) -> None:
    if not math.isfinite(value) or value < low or value > high:
        raise FacilityResidueSupplyError(
            f"{label} is outside [{low}, {high}]: {value!r}"
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
        raise FacilityResidueSupplyError(
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
        raise FacilityResidueSupplyError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _parse_rsyc_basis() -> RsvcBasis:
    if not RSYC_SUMMARY.is_file():
        raise FacilityResidueSupplyError(f"RSYC summary is missing: {RSYC_SUMMARY}")
    payload = json.loads(RSYC_SUMMARY.read_text(encoding="utf-8"))
    definitions = payload.get("definitions", {})
    lhv = _finite_nonnegative(
        definitions.get("lhv_gj_per_odt"),
        "RSYC lhv_gj_per_odt",
    )
    if lhv <= 0.0:
        raise FacilityResidueSupplyError("RSYC LHV must be positive")
    base_row = None
    for row in payload.get("harvest_sensitivity", []):
        if (
            float(row.get("harvest_level_million_m3_per_year", 0.0))
            == RSYC_100M_HARVEST_M3 / 1e6
            and str(row.get("case", "")) == "base"
        ):
            base_row = row
            break
    if base_row is None:
        raise FacilityResidueSupplyError(
            "RSYC summary has no base 100 million m3 harvest row"
        )
    base_odt = _finite_nonnegative(
        base_row.get("recoverable_residue_odt_per_year"),
        "RSYC 100m recoverable odt",
    )
    base_pj = _finite_nonnegative(
        base_row.get("recoverable_pj_per_year"),
        "RSYC 100m recoverable PJ",
    )
    _require_close(
        base_odt * lhv / PJ_GJ,
        base_pj,
        "RSYC 100m PJ reproduction",
    )
    return RsvcBasis(
        lhv_gj_per_odt=lhv,
        lhv_source=str(RSYC_SUMMARY.relative_to(REPO_ROOT)),
        base_100m_recoverable_odt=base_odt,
        base_100m_recoverable_pj=base_pj,
    )


def _parse_product_chain() -> tuple[dict[str, ProvinceChain], dict[str, Any]]:
    path = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
    required = [
        "province",
        "province_name",
        "harvest_m3",
        "primary_feedstock_m3",
        "residue_feedstock_m3",
        "total_feedstock_m3",
        *CLASS_FEEDSTOCK_COLUMN.values(),
        *CLASS_PRODUCT_COLUMN.values(),
        *CLASS_RESIDUE_COLUMN.values(),
        "process_residue_odt",
        "harvest_residue_gross_m3",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_m3",
        "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_m3",
        "harvest_residue_unrecovered_odt",
        "rsyc_base_recoverable_odt",
        "model_recoverable_odt_base",
        "rsyc_ratio",
        "rsyc_status",
        "coefficient_tier",
        "coefficient_source",
        "model_estimate",
        "observed",
        "policy_grade",
        "facility_assignment",
    ]
    frame = _read_csv(path, required, "product-chain province table")
    summary_path = PRODUCT_CHAIN_DIR / "summary.json"
    if not summary_path.is_file():
        raise FacilityResidueSupplyError(
            f"product-chain summary is missing: {summary_path}"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("artifact") != PRODUCT_CHAIN_ARTIFACT:
        raise FacilityResidueSupplyError(
            f"unexpected product-chain artifact: {summary.get('artifact')!r}"
        )
    if summary.get("status") != "pass":
        raise FacilityResidueSupplyError("product chain status is not pass")
    if summary.get("model_estimate") is not True:
        raise FacilityResidueSupplyError("product chain is not model_estimate")
    if summary.get("observed") is not False:
        raise FacilityResidueSupplyError("product chain is not observed=false")
    if summary.get("policy_grade") is not False:
        raise FacilityResidueSupplyError("product chain is not policy_grade=false")
    if summary.get("facility_assignment") is not False:
        raise FacilityResidueSupplyError(
            "product chain unexpectedly contains a facility assignment"
        )

    frame["province"] = frame["province"].astype(str).str.strip().str.upper()
    provinces = set(frame["province"])
    if provinces != set(PROVINCES):
        missing = set(PROVINCES) - provinces
        extra = provinces - set(PROVINCES)
        raise FacilityResidueSupplyError(
            f"product-chain province set mismatch; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    if frame["province"].duplicated().any():
        raise FacilityResidueSupplyError(
            "product-chain province table has duplicates"
        )

    chains: dict[str, ProvinceChain] = {}
    for _, record in frame.iterrows():
        province = str(record["province"]).upper()
        harvest = _finite_nonnegative(
            record["harvest_m3"], f"{province} harvest_m3"
        )
        primary = _finite_nonnegative(
            record["primary_feedstock_m3"], f"{province} primary_feedstock_m3"
        )
        residue_feedstock = _finite_nonnegative(
            record["residue_feedstock_m3"], f"{province} residue_feedstock_m3"
        )
        total_feedstock = _finite_nonnegative(
            record["total_feedstock_m3"], f"{province} total_feedstock_m3"
        )
        _require_close(
            primary + residue_feedstock,
            total_feedstock,
            f"{province} primary plus residue feedstock",
        )
        class_feedstock = {
            facility_class: _finite_nonnegative(
                record[CLASS_FEEDSTOCK_COLUMN[facility_class]],
                f"{province}/{facility_class} feedstock",
            )
            for facility_class in CLASS_ORDER
        }
        _require_close(
            math.fsum(class_feedstock.values()),
            total_feedstock,
            f"{province} class feedstock conservation",
        )
        _require_close(
            primary,
            harvest,
            f"{province} primary feedstock versus harvest",
        )
        class_product = {
            facility_class: _finite_nonnegative(
                record[CLASS_PRODUCT_COLUMN[facility_class]],
                f"{province}/{facility_class} product",
            )
            for facility_class in CLASS_ORDER
        }
        class_residue = {
            facility_class: _finite_nonnegative(
                record[CLASS_RESIDUE_COLUMN[facility_class]],
                f"{province}/{facility_class} residue",
            )
            for facility_class in CLASS_ORDER
        }
        process_residue = _finite_nonnegative(
            record["process_residue_odt"], f"{province} process_residue_odt"
        )
        _require_close(
            math.fsum(class_residue.values()),
            process_residue,
            f"{province} process residue conservation",
        )
        harvest_gross_odt = _finite_nonnegative(
            record["harvest_residue_gross_odt"],
            f"{province} harvest_residue_gross_odt",
        )
        harvest_recoverable_odt = _finite_nonnegative(
            record["harvest_residue_recoverable_odt"],
            f"{province} harvest_residue_recoverable_odt",
        )
        harvest_unrecovered_odt = _finite_nonnegative(
            record["harvest_residue_unrecovered_odt"],
            f"{province} harvest_residue_unrecovered_odt",
        )
        _require_close(
            harvest_recoverable_odt + harvest_unrecovered_odt,
            harvest_gross_odt,
            f"{province} harvest residue conservation",
        )
        rsyc_base = _finite_nonnegative(
            record["rsyc_base_recoverable_odt"],
            f"{province} rsyc_base_recoverable_odt",
        )
        model_recoverable = _finite_nonnegative(
            record["model_recoverable_odt_base"],
            f"{province} model_recoverable_odt_base",
        )
        _require_close(
            model_recoverable,
            harvest_gross_odt * RSYC_BASE_RECOVERY,
            f"{province} model recoverable harvest residue",
        )
        rsyc_ratio = _finite_nonnegative(
            record["rsyc_ratio"], f"{province} rsyc_ratio"
        )
        if rsyc_base > 0.0:
            _require_close(
                rsyc_ratio,
                model_recoverable / rsyc_base,
                f"{province} RSYC ratio",
            )
        if harvest > 0.0 and rsyc_base <= 0.0:
            raise FacilityResidueSupplyError(
                f"{province}: RSYC base is zero with positive harvest"
            )
        model_estimate = _parse_bool(
            record["model_estimate"], f"{province} model_estimate"
        )
        observed = _parse_bool(record["observed"], f"{province} observed")
        policy_grade = _parse_bool(
            record["policy_grade"], f"{province} policy_grade"
        )
        facility_assignment = _parse_bool(
            record["facility_assignment"], f"{province} facility_assignment"
        )
        if not model_estimate or observed or policy_grade or facility_assignment:
            raise FacilityResidueSupplyError(
                f"{province} product-chain labels are not screening labels"
            )
        coefficient_tier = str(record["coefficient_tier"]).strip()
        if coefficient_tier not in {
            "bc_anchor_species_group",
            "donor_coefficient",
            "measured_bucking_pilot",
            "e2_open_data_imputation",
        }:
            raise FacilityResidueSupplyError(
                f"{province} unexpected coefficient tier {coefficient_tier!r}"
            )
        chains[province] = ProvinceChain(
            province=province,
            province_name=str(record["province_name"]).strip(),
            harvest_m3=harvest,
            class_feedstock_m3=class_feedstock,
            class_product=class_product,
            class_residue_odt=class_residue,
            process_residue_odt=process_residue,
            harvest_residue_gross_m3=_finite_nonnegative(
                record["harvest_residue_gross_m3"],
                f"{province} harvest_residue_gross_m3",
            ),
            harvest_residue_gross_odt=harvest_gross_odt,
            harvest_residue_recoverable_m3=_finite_nonnegative(
                record["harvest_residue_recoverable_m3"],
                f"{province} harvest_residue_recoverable_m3",
            ),
            harvest_residue_recoverable_odt=harvest_recoverable_odt,
            harvest_residue_unrecovered_m3=_finite_nonnegative(
                record["harvest_residue_unrecovered_m3"],
                f"{province} harvest_residue_unrecovered_m3",
            ),
            harvest_residue_unrecovered_odt=harvest_unrecovered_odt,
            rsyc_base_recoverable_odt=rsyc_base,
            model_recoverable_odt_base=model_recoverable,
            rsyc_ratio=rsyc_ratio,
            rsyc_status=str(record["rsyc_status"]).strip(),
            coefficient_tier=coefficient_tier,
            coefficient_source=str(record["coefficient_source"]).strip(),
        )

    national_harvest = math.fsum(
        chain.harvest_m3 for chain in chains.values()
    )
    _require_close(
        national_harvest,
        float(summary["harvest_total_m3"]),
        "product-chain harvest total",
    )
    national = summary.get("national_totals", {})
    _require_close(
        math.fsum(chain.process_residue_odt for chain in chains.values()),
        float(national["process_residue_odt"]),
        "product-chain process residue total",
    )
    _require_close(
        math.fsum(
            chain.harvest_residue_recoverable_odt
            for chain in chains.values()
        ),
        float(national["harvest_residue_recoverable_odt"]),
        "product-chain recoverable harvest residue total",
    )
    _require_close(
        math.fsum(
            chain.rsyc_base_recoverable_odt for chain in chains.values()
        ),
        float(national["rsyc_base_recoverable_odt"]),
        "product-chain RSYC base total",
    )
    return chains, summary


def _parse_assignment() -> tuple[list[AssignmentLink], dict[str, Any]]:
    path = ASSIGNMENT_DIR / "facility_assignment.csv"
    required = [
        "scenario",
        "province",
        "facility_id",
        "facility_name",
        "facility_class",
        "facility_province",
        "cross_border",
        "capacity_status",
        "capacity_evidence",
        "floor_scale_factor",
        "capacity_m3_y",
        "assigned_m3",
        "utilization",
        "evidence_tier",
        "evidence_label",
        "observed",
        "policy_grade",
    ]
    frame = _read_csv(
        path, required, f"facility assignment {ASSIGNMENT_VERSION}"
    )
    frame = frame[
        frame["scenario"].astype(str).str.strip() == SCENARIO
    ].copy()
    if frame.empty:
        raise FacilityResidueSupplyError(
            f"{ASSIGNMENT_LABEL} has no rows for scenario {SCENARIO!r}"
        )
    summary_path = ASSIGNMENT_DIR / "summary.json"
    if not summary_path.is_file():
        raise FacilityResidueSupplyError(
            f"{ASSIGNMENT_LABEL} summary is missing: {summary_path}"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("artifact") != ASSIGNMENT_ARTIFACT:
        raise FacilityResidueSupplyError(
            f"unexpected assignment artifact: {summary.get('artifact')!r}"
        )
    if SCENARIO not in summary.get("scenarios", {}):
        raise FacilityResidueSupplyError(
            f"assignment summary lacks scenario {SCENARIO!r}"
        )
    labels = summary.get("labels", {})
    if labels.get("observed") is not False or labels.get("policy_grade") is not False:
        raise FacilityResidueSupplyError(
            f"{ASSIGNMENT_LABEL} labels are not screening labels"
        )

    links: list[AssignmentLink] = []
    seen_origins: set[tuple[str, str]] = set()
    facility_meta: dict[str, tuple[Any, ...]] = {}
    cumulative_assigned: dict[str, float] = defaultdict(float)
    for position, record in frame.iterrows():
        origin_province = str(record["province"]).strip().upper()
        if origin_province not in PROVINCES:
            raise FacilityResidueSupplyError(
                f"assignment row {position}: unexpected province "
                f"{origin_province!r}"
            )
        facility_class = str(record["facility_class"]).strip().upper()
        if facility_class not in CLASS_ORDER:
            raise FacilityResidueSupplyError(
                f"assignment row {position}: unexpected facility_class "
                f"{facility_class!r}"
            )
        facility_province = str(record["facility_province"]).strip().upper()
        if facility_province not in PROVINCES:
            raise FacilityResidueSupplyError(
                f"assignment row {position}: unexpected facility province "
                f"{facility_province!r}"
            )
        facility_id = str(record["facility_id"]).strip()
        if not facility_id:
            raise FacilityResidueSupplyError(
                f"assignment row {position}: empty facility_id"
            )
        origin_key = (origin_province, facility_id)
        if origin_key in seen_origins:
            raise FacilityResidueSupplyError(
                f"duplicate assignment row for {origin_key}"
            )
        seen_origins.add(origin_key)
        capacity_status = str(record["capacity_status"]).strip().lower()
        if capacity_status not in CAPACITY_STATUSES:
            raise FacilityResidueSupplyError(
                f"{facility_id}: unexpected capacity status {capacity_status!r}"
            )
        capacity_evidence = str(record["capacity_evidence"]).strip()
        if capacity_evidence not in CAPACITY_EVIDENCE_ALLOWED[capacity_status]:
            raise FacilityResidueSupplyError(
                f"{facility_id}: capacity evidence {capacity_evidence!r} "
                f"does not match {capacity_status!r}"
            )
        capacity = _finite_nonnegative(
            record["capacity_m3_y"], f"{facility_id} capacity_m3_y"
        )
        if capacity <= 0.0:
            raise FacilityResidueSupplyError(
                f"{facility_id}: assignment row has non-positive capacity"
            )
        assigned = _finite_nonnegative(
            record["assigned_m3"], f"{facility_id} assigned_m3"
        )
        if assigned <= 0.0:
            raise FacilityResidueSupplyError(
                f"{facility_id}: assignment row has non-positive assigned volume"
            )
        if assigned > capacity * (1.0 + 1e-9) + 1e-6:
            raise FacilityResidueSupplyError(
                f"{facility_id}: assigned {assigned} exceeds capacity {capacity}"
            )
        utilization = _finite_nonnegative(
            record["utilization"], f"{facility_id} utilization"
        )
        _require_between(
            utilization, 0.0, 1.0 + 1e-9, f"{facility_id} utilization"
        )
        cumulative_assigned[facility_id] += assigned
        expected_utilization = (
            cumulative_assigned[facility_id]
            if UTILIZATION_MODE == "cumulative"
            else assigned
        ) / capacity
        _require_close(
            utilization,
            expected_utilization,
            f"{facility_id} utilization reproduction",
        )
        scale = _finite_nonnegative(
            record["floor_scale_factor"], f"{facility_id} floor_scale_factor"
        )
        if scale <= 0.0:
            raise FacilityResidueSupplyError(
                f"{facility_id}: floor scale factor must be positive"
            )
        if capacity_status == PUBLISHED:
            _require_close(scale, 1.0, f"{facility_id} published scale")
        if capacity_status == PROXY:
            _require_close(scale, 1.0, f"{facility_id} proxy scale")
        if capacity_status == SCALED_PROXY and scale <= 1.0 + 1e-12:
            raise FacilityResidueSupplyError(
                f"{facility_id}: scaled proxy must have scale > 1"
            )
        if str(record["evidence_label"]).strip() != EVIDENCE_LABEL:
            raise FacilityResidueSupplyError(
                f"{facility_id}: unexpected evidence label"
            )
        if _parse_bool(record["observed"], f"{facility_id} observed"):
            raise FacilityResidueSupplyError(
                f"{facility_id}: assignment row is unexpectedly observed"
            )
        if _parse_bool(record["policy_grade"], f"{facility_id} policy_grade"):
            raise FacilityResidueSupplyError(
                f"{facility_id}: assignment row is unexpectedly policy grade"
            )
        evidence_tier = str(record["evidence_tier"]).strip()
        if not evidence_tier:
            raise FacilityResidueSupplyError(
                f"{facility_id}: missing assignment evidence tier"
            )
        meta = (
            facility_class,
            facility_province,
            capacity_status,
            capacity_evidence,
            scale,
            capacity,
        )
        previous = facility_meta.get(facility_id)
        if previous is not None and previous != meta:
            raise FacilityResidueSupplyError(
                f"{facility_id}: facility metadata changes across rows"
            )
        facility_meta[facility_id] = meta
        links.append(
            AssignmentLink(
                origin_province=origin_province,
                facility_id=facility_id,
                facility_name=str(record["facility_name"]).strip(),
                facility_class=facility_class,
                facility_province=facility_province,
                cross_border=_parse_bool(
                    record["cross_border"], f"{facility_id} cross_border"
                ),
                capacity_status=capacity_status,
                capacity_evidence=capacity_evidence,
                floor_scale_factor=scale,
                capacity_m3_y=capacity,
                assigned_m3=assigned,
                utilization=utilization,
                evidence_tier=evidence_tier,
            )
        )

    scenario = summary["scenarios"][SCENARIO]
    assigned_total = math.fsum(link.assigned_m3 for link in links)
    _require_close(
        assigned_total,
        float(scenario["assigned_m3"]),
        f"{SCENARIO} assigned total",
    )
    unassigned = float(scenario.get("unassigned_m3", 0.0))
    if abs(unassigned) > ABS_TOL:
        raise FacilityResidueSupplyError(
            f"{SCENARIO}: unassigned volume is {unassigned!r}"
        )
    facilities_used = {link.facility_id for link in links}
    if len(facilities_used) != int(scenario["facilities_used"]):
        raise FacilityResidueSupplyError(
            f"{SCENARIO}: facilities used {len(facilities_used)} does not "
            f"match summary {scenario['facilities_used']}"
        )
    proxy_assigned = math.fsum(
        link.assigned_m3
        for link in links
        if link.capacity_status != PUBLISHED
    )
    proxy_share = proxy_assigned / assigned_total if assigned_total > 0.0 else 0.0
    _require_close(
        proxy_share,
        float(scenario.get("proxy_share_of_assigned", proxy_share)),
        f"{SCENARIO} proxy share",
    )
    cross_border_assigned = math.fsum(
        link.assigned_m3 for link in links if link.cross_border
    )
    cross_share = (
        cross_border_assigned / assigned_total if assigned_total > 0.0 else 0.0
    )
    _require_close(
        cross_share,
        float(scenario.get("cross_border_share", cross_share)),
        f"{SCENARIO} cross-border share",
    )
    expected_cross_border = math.fsum(
        link.assigned_m3
        for link in links
        if link.facility_province != link.origin_province
    )
    _require_close(
        expected_cross_border,
        cross_border_assigned,
        "cross-border flag versus province",
    )
    return links, summary


def _allocation_pool(
    province: str,
    facility_class: str,
    local_links: Sequence[AssignmentLink],
    national_links: Sequence[AssignmentLink],
) -> tuple[list[AssignmentLink], str]:
    if local_links:
        return list(local_links), "province_assignment"
    if not national_links:
        raise FacilityResidueSupplyError(
            f"no assigned {facility_class} facilities exist nationally for "
            f"{province} class feedstock"
        )
    return list(national_links), "national_class_fallback"


def _allocate(
    chains: Mapping[str, ProvinceChain],
    links: Sequence[AssignmentLink],
    rsvc: RsvcBasis,
) -> list[ResidueAllocation]:
    local_by_origin_class: dict[
        tuple[str, str], list[AssignmentLink]
    ] = defaultdict(list)
    national_by_class: dict[str, list[AssignmentLink]] = defaultdict(list)
    for link in links:
        local_by_origin_class[
            (link.origin_province, link.facility_class)
        ].append(link)
        national_by_class[link.facility_class].append(link)

    allocations: list[ResidueAllocation] = []
    for province in PROVINCES:
        chain = chains[province]
        for facility_class in CLASS_ORDER:
            feedstock = chain.class_feedstock_m3[facility_class]
            if feedstock <= 0.0:
                continue
            pool, scope = _allocation_pool(
                province,
                facility_class,
                local_by_origin_class.get((province, facility_class), []),
                national_by_class.get(facility_class, []),
            )
            total_weight = math.fsum(link.assigned_m3 for link in pool)
            if total_weight <= 0.0:
                raise FacilityResidueSupplyError(
                    f"{province}/{facility_class}: allocation weight is zero"
                )
            product_reference = chain.class_product[facility_class]
            residue_reference = chain.class_residue_odt[facility_class]
            product_per_m3 = product_reference / feedstock
            residue_odt_per_m3 = residue_reference / feedstock
            group: list[ResidueAllocation] = []
            for link in pool:
                if link.facility_class != facility_class:
                    raise FacilityResidueSupplyError(
                        f"{link.facility_id}: class mixing is not permitted"
                    )
                share = link.assigned_m3 / total_weight
                _require_between(share, 0.0, 1.0, "allocation share")
                volume = feedstock * share
                product = volume * product_per_m3
                residue_odt = volume * residue_odt_per_m3
                residue_pj = residue_odt * rsvc.lhv_gj_per_odt / PJ_GJ
                if not all(
                    math.isfinite(value) and value >= 0.0
                    for value in (volume, product, residue_odt, residue_pj)
                ):
                    raise FacilityResidueSupplyError(
                        f"{province}/{facility_class}/{link.facility_id}: "
                        "invalid allocation quantity"
                    )
                group.append(
                    ResidueAllocation(
                        origin_province=province,
                        facility_id=link.facility_id,
                        facility_name=link.facility_name,
                        facility_class=facility_class,
                        feedstock_class=facility_class,
                        facility_province=link.facility_province,
                        cross_border=link.facility_province != province,
                        allocation_scope=scope,
                        assignment_weight_m3=link.assigned_m3,
                        assignment_share=share,
                        capacity_status=link.capacity_status,
                        capacity_evidence=link.capacity_evidence,
                        floor_scale_factor=link.floor_scale_factor,
                        capacity_m3_y=link.capacity_m3_y,
                        assigned_m3=link.assigned_m3,
                        utilization=link.utilization,
                        feedstock_m3=volume,
                        product_quantity=product,
                        product_unit=CLASS_PRODUCT_UNIT[facility_class],
                        product_per_m3=product_per_m3,
                        residue_odt=residue_odt,
                        residue_pj=residue_pj,
                        residue_odt_per_m3=residue_odt_per_m3,
                        coefficient_tier=chain.coefficient_tier,
                    )
                )
            _require_close(
                math.fsum(item.feedstock_m3 for item in group),
                feedstock,
                f"{province}/{facility_class} facility feedstock conservation",
            )
            _require_close(
                math.fsum(item.product_quantity for item in group),
                product_reference,
                f"{province}/{facility_class} facility product conservation",
            )
            _require_close(
                math.fsum(item.residue_odt for item in group),
                residue_reference,
                f"{province}/{facility_class} facility residue conservation",
            )
            allocations.extend(group)
    return allocations


def _facility_residue_totals(
    allocations: Sequence[ResidueAllocation],
) -> tuple[dict[str, float], dict[str, ResidueAllocation]]:
    totals: dict[str, float] = defaultdict(float)
    meta: dict[str, ResidueAllocation] = {}
    for allocation in allocations:
        key = allocation.facility_id
        previous = meta.get(key)
        if previous is None:
            meta[key] = allocation
        elif (
            previous.facility_class != allocation.facility_class
            or previous.facility_province != allocation.facility_province
            or previous.capacity_status != allocation.capacity_status
            or previous.capacity_m3_y != allocation.capacity_m3_y
        ):
            raise FacilityResidueSupplyError(
                f"{key}: facility metadata changes across allocations"
            )
        totals[key] += allocation.residue_odt
    return dict(totals), meta


def _proxy_shares(
    allocations: Sequence[ResidueAllocation],
) -> dict[str, float]:
    total = math.fsum(item.residue_odt for item in allocations)
    if total <= 0.0:
        return {
            "published": 0.0,
            "proxy": 0.0,
            "proxy_harvest_scaled": 0.0,
            "proxy_combined": 0.0,
        }
    values = {
        status: math.fsum(
            item.residue_odt
            for item in allocations
            if item.capacity_status == status
        )
        / total
        for status in CAPACITY_STATUSES
    }
    values["proxy_combined"] = values[PROXY] + values[SCALED_PROXY]
    return values


def _concentration(
    allocations: Sequence[ResidueAllocation],
    facility_totals: Mapping[str, float],
    facility_meta: Mapping[str, ResidueAllocation],
) -> dict[str, Any]:
    total = math.fsum(facility_totals.values())
    if total <= 0.0:
        raise FacilityResidueSupplyError("facility process residue total is zero")
    ordered = sorted(
        facility_totals.items(), key=lambda item: item[1], reverse=True
    )
    shares = [value / total for _, value in ordered]
    hhi = math.fsum(share * share for share in shares)
    proxy_totals = _proxy_shares(allocations)
    class_summary: dict[str, Any] = {}
    for facility_class in CLASS_ORDER:
        class_total = math.fsum(
            value
            for facility_id, value in facility_totals.items()
            if facility_meta[facility_id].facility_class == facility_class
        )
        class_proxy = math.fsum(
            value
            for facility_id, value in facility_totals.items()
            if facility_meta[facility_id].facility_class == facility_class
            and facility_meta[facility_id].capacity_status != PUBLISHED
        )
        class_summary[facility_class] = {
            "residue_odt": class_total,
            "share_of_process_residue": class_total / total,
            "facilities": sum(
                1
                for facility_id in facility_totals
                if facility_meta[facility_id].facility_class == facility_class
            ),
            "proxy_residue_share": (
                class_proxy / class_total if class_total > 0.0 else 0.0
            ),
        }
    return {
        "facilities_used": len(facility_totals),
        "facilities_with_positive_residue": sum(
            1 for value in facility_totals.values() if value > 0.0
        ),
        "process_residue_odt": total,
        "hhi": hhi,
        "effective_facility_count": 1.0 / hhi if hhi > 0.0 else 0.0,
        "top1_share": shares[0],
        "top5_share": math.fsum(shares[:5]),
        "published_residue_share": proxy_totals["published"],
        "proxy_residue_share": proxy_totals["proxy_combined"],
        "scaled_proxy_residue_share": proxy_totals[SCALED_PROXY],
        "by_class": class_summary,
    }


def _build_province_rows(
    chains: Mapping[str, ProvinceChain],
    allocations: Sequence[ResidueAllocation],
    facility_totals: Mapping[str, float],
    facility_meta: Mapping[str, ResidueAllocation],
    rsvc: RsvcBasis,
) -> list[dict[str, Any]]:
    by_facility_province: dict[str, list[ResidueAllocation]] = defaultdict(list)
    by_origin: dict[str, list[ResidueAllocation]] = defaultdict(list)
    for allocation in allocations:
        by_facility_province[allocation.facility_province].append(allocation)
        by_origin[allocation.origin_province].append(allocation)

    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        chain = chains[province]
        province_allocations = by_facility_province.get(province, [])
        origin_allocations = by_origin.get(province, [])
        process_odt = math.fsum(item.residue_odt for item in province_allocations)
        process_pj = math.fsum(item.residue_pj for item in province_allocations)
        origin_odt = math.fsum(item.residue_odt for item in origin_allocations)
        origin_pj = math.fsum(item.residue_pj for item in origin_allocations)
        facility_ids = {
            item.facility_id for item in province_allocations
        }
        positive_ids = {
            item.facility_id
            for item in province_allocations
            if item.residue_odt > 0.0
        }
        proxy = _proxy_shares(province_allocations)
        facility_residue = {
            facility_id: facility_totals[facility_id] for facility_id in facility_ids
        }
        if process_odt > 0.0:
            shares = sorted(
                (
                    value / process_odt
                    for value in facility_residue.values()
                ),
                reverse=True,
            )
            hhi = math.fsum(share * share for share in shares)
            top1 = shares[0]
        else:
            hhi = 0.0
            top1 = 0.0
        statuses = sorted(
            {item.capacity_status for item in province_allocations}
        )
        rows.append(
            {
                "province": province,
                "province_name": chain.province_name,
                "residue_type": "process_residue",
                "residue_odt": process_odt,
                "residue_pj": process_pj,
                "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
                "residue_province_basis": "facility_province",
                "facility_assigned": LABEL_TRUE,
                "facility_count": len(facility_ids),
                "facility_count_positive_residue": len(positive_ids),
                "origin_process_residue_odt": origin_odt,
                "origin_process_residue_pj": origin_pj,
                "published_residue_share": proxy["published"],
                "proxy_residue_share": proxy["proxy_combined"],
                "scaled_proxy_residue_share": proxy[SCALED_PROXY],
                "top1_facility_residue_share": top1,
                "hhi_facility_residue": hhi,
                "evidence_tier": chain.coefficient_tier,
                "capacity_status_summary": ";".join(statuses),
                "rsyc_comparable": LABEL_FALSE,
                "rsyc_base_recoverable_odt": 0.0,
                "model_recoverable_odt_base": 0.0,
                "rsyc_ratio": 0.0,
                "rsyc_status": "not_applicable",
                "model_estimate": LABEL_TRUE,
                "observed": LABEL_FALSE,
                "policy_grade": LABEL_FALSE,
                "notes": (
                    "Process residue generated at facilities located in the "
                    "province; origin_process columns show residue attributable "
                    "to feedstock harvested in the province."
                ),
            }
        )
        harvest_specs = (
            (
                "harvest_residue_gross",
                chain.harvest_residue_gross_odt,
                chain.harvest_residue_gross_m3,
                False,
                False,
                "Gross harvest residue from provincial harvest; not facility-assigned.",
            ),
            (
                "harvest_residue_recoverable",
                chain.harvest_residue_recoverable_odt,
                chain.harvest_residue_recoverable_m3,
                True,
                False,
                "Recoverable harvest residue from provincial harvest; RSYC-comparable and not facility-assigned.",
            ),
            (
                "harvest_residue_unrecovered",
                chain.harvest_residue_unrecovered_odt,
                chain.harvest_residue_unrecovered_m3,
                False,
                False,
                "Unrecovered share of gross harvest residue; not facility-assigned.",
            ),
        )
        for residue_type, odt, m3, comparable, facility_flag, note in harvest_specs:
            rows.append(
                {
                    "province": province,
                    "province_name": chain.province_name,
                    "residue_type": residue_type,
                    "residue_odt": odt,
                    "residue_pj": odt * rsvc.lhv_gj_per_odt / PJ_GJ,
                    "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
                    "residue_province_basis": "origin_province",
                    "facility_assigned": (
                        LABEL_TRUE if facility_flag else LABEL_FALSE
                    ),
                    "facility_count": 0,
                    "facility_count_positive_residue": 0,
                    "origin_process_residue_odt": 0.0,
                    "origin_process_residue_pj": 0.0,
                    "published_residue_share": 0.0,
                    "proxy_residue_share": 0.0,
                    "scaled_proxy_residue_share": 0.0,
                    "top1_facility_residue_share": 0.0,
                    "hhi_facility_residue": 0.0,
                    "evidence_tier": chain.coefficient_tier,
                    "capacity_status_summary": "",
                    "rsyc_comparable": (
                        LABEL_TRUE if comparable else LABEL_FALSE
                    ),
                    "rsyc_base_recoverable_odt": chain.rsyc_base_recoverable_odt,
                    "model_recoverable_odt_base": chain.model_recoverable_odt_base,
                    "rsyc_ratio": chain.rsyc_ratio,
                    "rsyc_status": chain.rsyc_status,
                    "model_estimate": LABEL_TRUE,
                    "observed": LABEL_FALSE,
                    "policy_grade": LABEL_FALSE,
                    "notes": (
                        f"{note} Residue volume {m3:.3f} m3 at "
                        f"{RESIDUE_DENSITY_ODT_M3} odt/m3."
                    ),
                }
            )
    return rows


def _national_totals(
    chains: Mapping[str, ProvinceChain],
    allocations: Sequence[ResidueAllocation],
    province_rows: Sequence[Mapping[str, Any]],
    concentration: Mapping[str, Any],
    rsvc: RsvcBasis,
) -> dict[str, float]:
    process_odt = math.fsum(item.residue_odt for item in allocations)
    process_pj = math.fsum(item.residue_pj for item in allocations)
    harvest_gross_odt = math.fsum(
        chain.harvest_residue_gross_odt for chain in chains.values()
    )
    harvest_recoverable_odt = math.fsum(
        chain.harvest_residue_recoverable_odt for chain in chains.values()
    )
    harvest_unrecovered_odt = math.fsum(
        chain.harvest_residue_unrecovered_odt for chain in chains.values()
    )
    rsyc_base_odt = math.fsum(
        chain.rsyc_base_recoverable_odt for chain in chains.values()
    )
    model_recoverable_odt = math.fsum(
        chain.model_recoverable_odt_base for chain in chains.values()
    )
    rsyc_ratio = (
        model_recoverable_odt / rsyc_base_odt if rsyc_base_odt > 0.0 else 0.0
    )
    if rsyc_ratio < 0.80:
        rsyc_status = "model_below_rsyc"
    elif rsyc_ratio > 1.25:
        rsyc_status = "model_above_rsyc"
    else:
        rsyc_status = "within_rsyc_range"
    fallback_odt = math.fsum(
        item.residue_odt
        for item in allocations
        if item.allocation_scope == "national_class_fallback"
    )
    cross_border_odt = math.fsum(
        item.residue_odt for item in allocations if item.cross_border
    )
    totals = {
        "total_feedstock_m3": math.fsum(
            item.feedstock_m3 for item in allocations
        ),
        "process_residue_odt": process_odt,
        "process_residue_pj": process_pj,
        "fallback_residue_odt": fallback_odt,
        "fallback_residue_share": (
            fallback_odt / process_odt if process_odt > 0.0 else 0.0
        ),
        "cross_border_residue_odt": cross_border_odt,
        "cross_border_residue_share": (
            cross_border_odt / process_odt if process_odt > 0.0 else 0.0
        ),
        "harvest_residue_gross_odt": harvest_gross_odt,
        "harvest_residue_gross_pj": (
            harvest_gross_odt * rsvc.lhv_gj_per_odt / PJ_GJ
        ),
        "harvest_residue_recoverable_odt": harvest_recoverable_odt,
        "harvest_residue_recoverable_pj": (
            harvest_recoverable_odt * rsvc.lhv_gj_per_odt / PJ_GJ
        ),
        "harvest_residue_unrecovered_odt": harvest_unrecovered_odt,
        "harvest_residue_unrecovered_pj": (
            harvest_unrecovered_odt * rsvc.lhv_gj_per_odt / PJ_GJ
        ),
        "rsyc_base_119m_recoverable_odt": rsyc_base_odt,
        "rsyc_base_119m_recoverable_pj": (
            rsyc_base_odt * rsvc.lhv_gj_per_odt / PJ_GJ
        ),
        "rsyc_base_100m_recoverable_odt": rsvc.base_100m_recoverable_odt,
        "rsyc_base_100m_recoverable_pj": rsvc.base_100m_recoverable_pj,
        "model_recoverable_odt": model_recoverable_odt,
        "model_recoverable_pj": (
            model_recoverable_odt * rsvc.lhv_gj_per_odt / PJ_GJ
        ),
        "rsyc_ratio": rsyc_ratio,
        "combined_process_plus_harvest_recoverable_odt": (
            process_odt + harvest_recoverable_odt
        ),
        "combined_process_plus_harvest_recoverable_pj": (
            process_odt + harvest_recoverable_odt
        )
        * rsvc.lhv_gj_per_odt
        / PJ_GJ,
        "combined_process_plus_harvest_gross_odt": (
            process_odt + harvest_gross_odt
        ),
        "combined_process_plus_harvest_gross_pj": (
            process_odt + harvest_gross_odt
        )
        * rsvc.lhv_gj_per_odt
        / PJ_GJ,
        "facilities_used": float(concentration["facilities_used"]),
        "facilities_with_positive_residue": float(
            concentration["facilities_with_positive_residue"]
        ),
        "hhi_process_residue": float(concentration["hhi"]),
        "top1_process_residue_share": float(concentration["top1_share"]),
        "top5_process_residue_share": float(concentration["top5_share"]),
        "published_residue_share": float(
            concentration["published_residue_share"]
        ),
        "proxy_residue_share": float(concentration["proxy_residue_share"]),
        "scaled_proxy_residue_share": float(
            concentration["scaled_proxy_residue_share"]
        ),
        "process_residue_odt_per_m3_feedstock": (
            process_odt / math.fsum(item.feedstock_m3 for item in allocations)
            if allocations
            else 0.0
        ),
        "rsyc_status": rsyc_status,
    }
    province_process = math.fsum(
        float(row["residue_odt"])
        for row in province_rows
        if row["residue_type"] == "process_residue"
    )
    _require_close(
        province_process,
        process_odt,
        "province process residue aggregation",
    )
    _require_close(
        math.fsum(
            float(row["residue_odt"])
            for row in province_rows
            if row["residue_type"] == "harvest_residue_gross"
        ),
        harvest_gross_odt,
        "province gross harvest residue aggregation",
    )
    _require_close(
        math.fsum(
            float(row["residue_odt"])
            for row in province_rows
            if row["residue_type"] == "harvest_residue_recoverable"
        ),
        harvest_recoverable_odt,
        "province recoverable harvest residue aggregation",
    )
    return totals


def _national_summary_rows(
    totals: Mapping[str, float], rsvc: RsvcBasis
) -> list[dict[str, Any]]:
    specs = [
        (
            "lhv_gj_per_odt",
            "energy",
            "GJ/odt",
            rsvc.lhv_gj_per_odt,
            False,
            f"Dry LHV from {rsvc.lhv_source}.",
        ),
        (
            "total_feedstock_m3",
            "process_residue",
            "m3",
            totals["total_feedstock_m3"],
            False,
            "Class-matched feedstock allocated to facilities.",
        ),
        (
            "process_residue_odt",
            "process_residue",
            "odt",
            totals["process_residue_odt"],
            False,
            "Process residue generated at facilities; not RSYC harvest residue.",
        ),
        (
            "process_residue_pj",
            "process_residue",
            "PJ",
            totals["process_residue_pj"],
            False,
            "Process residue energy at the documented LHV.",
        ),
        (
            "harvest_residue_gross_odt",
            "harvest_residue_gross",
            "odt",
            totals["harvest_residue_gross_odt"],
            False,
            "Gross provincial harvest residue; not facility-assigned.",
        ),
        (
            "harvest_residue_gross_pj",
            "harvest_residue_gross",
            "PJ",
            totals["harvest_residue_gross_pj"],
            False,
            "Gross harvest residue energy at the documented LHV.",
        ),
        (
            "harvest_residue_recoverable_odt",
            "harvest_residue_recoverable",
            "odt",
            totals["harvest_residue_recoverable_odt"],
            True,
            "Recoverable provincial harvest residue; the RSYC-comparable stream.",
        ),
        (
            "harvest_residue_recoverable_pj",
            "harvest_residue_recoverable",
            "PJ",
            totals["harvest_residue_recoverable_pj"],
            True,
            "Recoverable harvest residue energy at the documented LHV.",
        ),
        (
            "harvest_residue_unrecovered_odt",
            "harvest_residue_unrecovered",
            "odt",
            totals["harvest_residue_unrecovered_odt"],
            False,
            "Unrecovered share of gross harvest residue.",
        ),
        (
            "harvest_residue_unrecovered_pj",
            "harvest_residue_unrecovered",
            "PJ",
            totals["harvest_residue_unrecovered_pj"],
            False,
            "Unrecovered harvest residue energy at the documented LHV.",
        ),
        (
            "rsyc_base_119m_recoverable_odt",
            "rsyc_base",
            "odt",
            totals["rsyc_base_119m_recoverable_odt"],
            True,
            "RSYC province-factor base applied to the 119 Mm3 harvest case.",
        ),
        (
            "rsyc_base_119m_recoverable_pj",
            "rsyc_base",
            "PJ",
            totals["rsyc_base_119m_recoverable_pj"],
            True,
            "RSYC 119 Mm3 base at the documented LHV; derived value.",
        ),
        (
            "rsyc_base_100m_recoverable_odt",
            "rsyc_base_reference",
            "odt",
            totals["rsyc_base_100m_recoverable_odt"],
            False,
            "Documented RSYC base at 100 Mm3; reference only.",
        ),
        (
            "rsyc_base_100m_recoverable_pj",
            "rsyc_base_reference",
            "PJ",
            totals["rsyc_base_100m_recoverable_pj"],
            False,
            "Documented RSYC 100 Mm3 base; 129.6709 PJ is this reference, not the 119 Mm3 value.",
        ),
        (
            "model_recoverable_odt",
            "harvest_residue_recoverable",
            "odt",
            totals["model_recoverable_odt"],
            True,
            "Product-chain gross harvest residue x 0.60 recovery.",
        ),
        (
            "model_recoverable_pj",
            "harvest_residue_recoverable",
            "PJ",
            totals["model_recoverable_pj"],
            True,
            "Model recoverable harvest residue energy.",
        ),
        (
            "rsyc_ratio",
            "harvest_residue_recoverable",
            "ratio",
            totals["rsyc_ratio"],
            True,
            "Model recoverable harvest residue divided by the RSYC 119 Mm3 base.",
        ),
        (
            "combined_process_plus_harvest_recoverable_odt",
            "combined",
            "odt",
            totals["combined_process_plus_harvest_recoverable_odt"],
            False,
            "Combined process plus recoverable harvest residue; explicitly not the RSYC comparison.",
        ),
        (
            "combined_process_plus_harvest_recoverable_pj",
            "combined",
            "PJ",
            totals["combined_process_plus_harvest_recoverable_pj"],
            False,
            "Combined process plus recoverable harvest energy; explicitly not the RSYC comparison.",
        ),
        (
            "combined_process_plus_harvest_gross_odt",
            "combined",
            "odt",
            totals["combined_process_plus_harvest_gross_odt"],
            False,
            "Combined process plus gross harvest residue; explicitly not the RSYC comparison.",
        ),
        (
            "combined_process_plus_harvest_gross_pj",
            "combined",
            "PJ",
            totals["combined_process_plus_harvest_gross_pj"],
            False,
            "Combined process plus gross harvest energy; explicitly not the RSYC comparison.",
        ),
        (
            "fallback_residue_odt",
            "process_residue",
            "odt",
            totals["fallback_residue_odt"],
            False,
            "Process residue from origin class streams routed to the national class fallback.",
        ),
        (
            "fallback_residue_share",
            "process_residue",
            "fraction",
            totals["fallback_residue_share"],
            False,
            "National class fallback share of process residue.",
        ),
        (
            "cross_border_residue_odt",
            "process_residue",
            "odt",
            totals["cross_border_residue_odt"],
            False,
            "Process residue generated in a province different from the origin province.",
        ),
        (
            "cross_border_residue_share",
            "process_residue",
            "fraction",
            totals["cross_border_residue_share"],
            False,
            "Cross-border share of process residue; class fallback can exceed assignment cross-border volume.",
        ),
        (
            "facilities_used",
            "process_residue",
            "count",
            totals["facilities_used"],
            False,
            "Facilities receiving class-matched feedstock.",
        ),
        (
            "facilities_with_positive_residue",
            "process_residue",
            "count",
            totals["facilities_with_positive_residue"],
            False,
            "Facilities with positive process residue.",
        ),
        (
            "hhi_process_residue",
            "process_residue",
            "index_0_1",
            totals["hhi_process_residue"],
            False,
            "Herfindahl-Hirschman index of facility process residue.",
        ),
        (
            "top1_process_residue_share",
            "process_residue",
            "fraction",
            totals["top1_process_residue_share"],
            False,
            "Largest facility share of process residue.",
        ),
        (
            "top5_process_residue_share",
            "process_residue",
            "fraction",
            totals["top5_process_residue_share"],
            False,
            "Five largest facility shares of process residue.",
        ),
        (
            "published_residue_share",
            "process_residue",
            "fraction",
            totals["published_residue_share"],
            False,
            "Process residue at published-capacity facilities.",
        ),
        (
            "proxy_residue_share",
            "process_residue",
            "fraction",
            totals["proxy_residue_share"],
            False,
            "Process residue at proxy and scaled-proxy facilities.",
        ),
        (
            "scaled_proxy_residue_share",
            "process_residue",
            "fraction",
            totals["scaled_proxy_residue_share"],
            False,
            "Process residue at proxy_harvest_scaled facilities.",
        ),
        (
            "process_residue_odt_per_m3_feedstock",
            "process_residue",
            "odt/m3",
            totals["process_residue_odt_per_m3_feedstock"],
            False,
            "National process residue ratio.",
        ),
    ]
    return [
        {
            "metric": metric,
            "residue_type": residue_type,
            "unit": unit,
            "value": float(value),
            "rsyc_comparable": LABEL_TRUE if comparable else LABEL_FALSE,
            "notes": notes,
        }
        for metric, residue_type, unit, value, comparable, notes in specs
    ]


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
        raise FacilityResidueSupplyError(f"{check_id}: {detail}")


def _validate(
    chains: Mapping[str, ProvinceChain],
    allocations: Sequence[ResidueAllocation],
    province_rows: Sequence[Mapping[str, Any]],
    national_rows: Sequence[Mapping[str, Any]],
    totals: Mapping[str, float],
    concentration: Mapping[str, Any],
    product_chain_summary: Mapping[str, Any],
    assignment_summary: Mapping[str, Any],
    rsvc: RsvcBasis,
    deletion_clean: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    mixed: list[str] = []
    facility_classes: dict[str, str] = {}
    for allocation in allocations:
        if (
            allocation.facility_class not in CLASS_ORDER
            or allocation.feedstock_class != allocation.facility_class
        ):
            mixed.append(allocation.facility_id)
        previous_class = facility_classes.get(allocation.facility_id)
        if previous_class is not None and previous_class != allocation.facility_class:
            mixed.append(f"{allocation.facility_id}: multiple classes")
        facility_classes[allocation.facility_id] = allocation.facility_class
    _check(
        checks,
        "class_matching",
        not mixed,
        f"{len(mixed)} class-mismatched allocation rows",
        allocation_rows=len(allocations),
    )

    class_feedstock_gaps: list[str] = []
    class_product_gaps: list[str] = []
    class_residue_gaps: list[str] = []
    for province in PROVINCES:
        chain = chains[province]
        group = [
            allocation
            for allocation in allocations
            if allocation.origin_province == province
        ]
        for facility_class in CLASS_ORDER:
            reference_feedstock = chain.class_feedstock_m3[facility_class]
            reference_product = chain.class_product[facility_class]
            reference_residue = chain.class_residue_odt[facility_class]
            actual_feedstock = math.fsum(
                item.feedstock_m3
                for item in group
                if item.facility_class == facility_class
            )
            actual_product = math.fsum(
                item.product_quantity
                for item in group
                if item.facility_class == facility_class
            )
            actual_residue = math.fsum(
                item.residue_odt
                for item in group
                if item.facility_class == facility_class
            )
            if abs(actual_feedstock - reference_feedstock) > REL_TOL * max(
                reference_feedstock, 1.0
            ):
                class_feedstock_gaps.append(f"{province}/{facility_class}")
            if abs(actual_product - reference_product) > REL_TOL * max(
                reference_product, 1.0
            ):
                class_product_gaps.append(f"{province}/{facility_class}")
            if abs(actual_residue - reference_residue) > REL_TOL * max(
                reference_residue, 1.0
            ):
                class_residue_gaps.append(f"{province}/{facility_class}")
    _check(
        checks,
        "province_class_feedstock_conservation",
        not class_feedstock_gaps,
        f"failed: {class_feedstock_gaps[:5]}",
    )
    _check(
        checks,
        "province_class_product_conservation",
        not class_product_gaps,
        f"failed: {class_product_gaps[:5]}",
    )
    _check(
        checks,
        "province_class_process_residue_conservation",
        not class_residue_gaps,
        f"failed: {class_residue_gaps[:5]}",
    )

    national = product_chain_summary["national_totals"]
    national_metric_map = {
        "total_feedstock_m3": "total_feedstock_m3",
        "process_residue_odt": "process_residue_odt",
        "harvest_residue_gross_odt": "harvest_residue_gross_odt",
        "harvest_residue_recoverable_odt": "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_odt": "harvest_residue_unrecovered_odt",
        "rsyc_base_recoverable_odt": "rsyc_base_119m_recoverable_odt",
        "model_recoverable_odt_base": "model_recoverable_odt",
    }
    for source_metric, total_metric in national_metric_map.items():
        expected = float(national[source_metric])
        actual = float(totals[total_metric])
        _require_close(
            actual, expected, f"product-chain summary {source_metric}"
        )
    _check(
        checks,
        "product_chain_summary_crosscheck",
        True,
        "facility totals reproduce product-chain summary national totals",
    )

    invalid: list[str] = []
    for allocation in allocations:
        for field in (
            "assignment_weight_m3",
            "assignment_share",
            "capacity_m3_y",
            "utilization",
            "feedstock_m3",
            "product_quantity",
            "residue_odt",
            "residue_pj",
            "residue_odt_per_m3",
        ):
            value = float(getattr(allocation, field))
            if not math.isfinite(value) or value < 0.0:
                invalid.append(f"{allocation.facility_id}.{field}")
    for row in province_rows:
        for key, value in row.items():
            if isinstance(value, float) and (
                not math.isfinite(value) or value < 0.0
            ):
                invalid.append(f"{row['province']}.{key}")
    _check(
        checks,
        "finite_nonnegative",
        not invalid,
        f"{len(invalid)} non-finite or negative values",
    )

    pj_errors: list[str] = []
    for allocation in allocations:
        expected = allocation.residue_odt * rsvc.lhv_gj_per_odt / PJ_GJ
        if abs(allocation.residue_pj - expected) > REL_TOL * max(expected, 1.0):
            pj_errors.append(allocation.facility_id)
    for row in province_rows:
        expected = float(row["residue_odt"]) * rsvc.lhv_gj_per_odt / PJ_GJ
        if abs(float(row["residue_pj"]) - expected) > REL_TOL * max(
            expected, 1.0
        ):
            pj_errors.append(f"{row['province']}.{row['residue_type']}")
    _check(
        checks,
        "odt_pj_conversion",
        not pj_errors,
        f"failed: {pj_errors[:5]}",
        lhv_gj_per_odt=rsvc.lhv_gj_per_odt,
    )

    harvest_errors: list[str] = []
    for row in province_rows:
        if row["residue_type"] == "process_residue":
            if row["facility_assigned"] != LABEL_TRUE:
                harvest_errors.append(f"{row['province']}: process not assigned")
            if row["residue_province_basis"] != "facility_province":
                harvest_errors.append(f"{row['province']}: process basis")
        else:
            if row["facility_assigned"] != LABEL_FALSE:
                harvest_errors.append(f"{row['province']}: harvest assigned")
            if row["residue_province_basis"] != "origin_province":
                harvest_errors.append(f"{row['province']}: harvest basis")
    for province in PROVINCES:
        gross = [
            float(row["residue_odt"])
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == "harvest_residue_gross"
        ]
        recoverable = [
            float(row["residue_odt"])
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == "harvest_residue_recoverable"
        ]
        unrecovered = [
            float(row["residue_odt"])
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == "harvest_residue_unrecovered"
        ]
        if len(gross) != 1 or len(recoverable) != 1 or len(unrecovered) != 1:
            harvest_errors.append(f"{province}: harvest row count")
            continue
        if abs(gross[0] - (recoverable[0] + unrecovered[0])) > REL_TOL * max(
            gross[0], 1.0
        ):
            harvest_errors.append(f"{province}: harvest balance")
    comparable_rows = [
        row for row in province_rows if row["rsyc_comparable"] == LABEL_TRUE
    ]
    if any(
        row["residue_type"] != "harvest_residue_recoverable"
        for row in comparable_rows
    ):
        harvest_errors.append("RSYC comparable rows include non-recoverable stream")
    recoverable_rows = [
        row
        for row in province_rows
        if row["residue_type"] == "harvest_residue_recoverable"
    ]
    if any(row["rsyc_comparable"] != LABEL_TRUE for row in recoverable_rows):
        harvest_errors.append("recoverable harvest rows missing RSYC flag")
    _check(
        checks,
        "harvest_residue_separate",
        not harvest_errors,
        f"failed: {harvest_errors[:5]}",
    )

    rsyc_base = math.fsum(
        chain.rsyc_base_recoverable_odt for chain in chains.values()
    )
    model_recoverable = math.fsum(
        chain.model_recoverable_odt_base for chain in chains.values()
    )
    _require_close(
        rsyc_base,
        float(national["rsyc_base_recoverable_odt"]),
        "RSYC 119m base reproduction",
    )
    _require_close(
        model_recoverable,
        float(national["model_recoverable_odt_base"]),
        "RSYC model reproduction",
    )
    _require_close(
        rsvc.base_100m_recoverable_pj,
        129.6709066255944,
        "RSYC documented 100m PJ reference",
    )
    ratio = model_recoverable / rsyc_base if rsyc_base > 0.0 else 0.0
    _require_close(ratio, float(totals["rsyc_ratio"]), "RSYC ratio")
    _check(
        checks,
        "rsyc_comparison",
        True,
        "RSYC 119m base and model recoverable harvest residue reproduce",
        rsyc_base_119m_recoverable_odt=rsyc_base,
        rsyc_base_119m_recoverable_pj=rsyc_base
        * rsvc.lhv_gj_per_odt
        / PJ_GJ,
        rsyc_base_100m_recoverable_pj=rsvc.base_100m_recoverable_pj,
        model_recoverable_odt=model_recoverable,
        ratio=ratio,
    )

    scenario = assignment_summary["scenarios"][SCENARIO]
    assignment_errors: list[str] = []
    if abs(float(scenario.get("unassigned_m3", 0.0))) > ABS_TOL:
        assignment_errors.append("scenario has unassigned volume")
    if float(scenario.get("assigned_m3", 0.0)) <= 0.0:
        assignment_errors.append("scenario assigned volume is zero")
    if str(scenario.get("scenario", "")) != SCENARIO:
        assignment_errors.append("scenario name mismatch")
    _check(
        checks,
        f"{ASSIGNMENT_SUMMARY_KEY}_scenario",
        not assignment_errors,
        f"failed: {assignment_errors}",
    )

    facility_totals, facility_meta = _facility_residue_totals(allocations)
    total = math.fsum(facility_totals.values())
    proxy = _proxy_shares(allocations)
    if total > 0.0:
        _require_close(
            proxy["proxy_combined"],
            float(concentration["proxy_residue_share"]),
            "proxy residue share",
        )
    _check(
        checks,
        "proxy_share",
        {
            facility_meta[facility_id].capacity_status
            for facility_id in facility_meta
        }.issubset(CAPACITY_STATUSES),
        f"proxy share {proxy['proxy_combined']:.6f}",
        proxy_residue_share=proxy["proxy_combined"],
        scaled_proxy_residue_share=proxy[SCALED_PROXY],
    )

    total_lookup = {
        row["metric"]: float(row["value"]) for row in national_rows
    }
    _require_close(
        total_lookup["process_residue_odt"],
        float(totals["process_residue_odt"]),
        "national summary process residue",
    )
    _require_close(
        total_lookup["harvest_residue_recoverable_odt"],
        float(totals["harvest_residue_recoverable_odt"]),
        "national summary recoverable harvest residue",
    )
    _require_close(
        total_lookup["rsyc_ratio"],
        float(totals["rsyc_ratio"]),
        "national summary RSYC ratio",
    )
    _check(
        checks,
        "national_summary",
        True,
        "national summary rows match computed totals",
    )

    label_errors: list[str] = []
    for row in national_rows:
        if row["rsyc_comparable"] not in {LABEL_TRUE, LABEL_FALSE}:
            label_errors.append(str(row["metric"]))
    for row in province_rows:
        if (
            row["model_estimate"] != LABEL_TRUE
            or row["observed"] != LABEL_FALSE
            or row["policy_grade"] != LABEL_FALSE
        ):
            label_errors.append(
                f"{row['province']}.{row['residue_type']}"
            )
    _check(
        checks,
        "evidence_labels",
        not label_errors,
        f"failed: {label_errors[:5]}",
    )

    _check(
        checks,
        "repository_hygiene",
        deletion_clean,
        "no unexpected D in parent or data git status" if deletion_clean
        else "unexpected deletion status",
    )
    return checks


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    if not rows:
        raise FacilityResidueSupplyError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise FacilityResidueSupplyError(
                            f"refusing to write non-finite {key}: {value!r}"
                        )
                    if value < 0.0:
                        raise FacilityResidueSupplyError(
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
    summary: Mapping[str, Any],
    facility_rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
) -> str:
    totals = summary["national_totals"]
    rsyc = summary["rsyc_comparison"]
    concentration = summary["concentration"]
    lines = [
        f"# {ARTIFACT_TITLE}",
        "",
        (
            "This artifact recomputes facility-level class feedstock, products, "
            f"and process residue on the {ASSIGNMENT_LABEL} "
            f"`{SCENARIO}` case, and reports provincial harvest "
            "residue alongside it. Process residue is facility-assigned; "
            "harvest residue is not."
        ),
        "",
        (
            "All values are screening model estimates: `model_estimate=true`, "
            "`observed=false`, `policy_grade=false`. The artifact is not an "
            "operating, policy, AAC, tenure, or investment statement."
        ),
        "",
        "## Facility residue",
        "",
        ("| Province | Facility | Class | Residue odt | Residue PJ | "
        "Residue type | Capacity status | Evidence tier |"),
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for row in sorted(
        facility_rows,
        key=lambda item: float(item["residue_odt"]),
        reverse=True,
    )[:20]:
        facility_label = row["facility_name"] or row["facility_id"]
        lines.append(
            f"| {row['province']} | {facility_label} | "
            f"{row['facility_class']} | {_format_number(float(row['residue_odt']))} | "
            f"{_format_number(float(row['residue_pj']))} | "
            f"{row['residue_type']} | {row['capacity_status']} | "
            f"{row['evidence_tier']} |"
        )
    lines.extend(
        [
            "",
            (
                f"Facility process residue is "
                f"{_format_number(totals['process_residue_odt'])} odt / "
                f"{_format_number(totals['process_residue_pj'])} PJ at "
                f"{rsyc['lhv_gj_per_odt']} GJ/odt. "
                f"{int(totals['facilities_used'])} facilities receive "
                "class-matched feedstock; "
                f"{int(totals['facilities_with_positive_residue'])} have "
                "positive process residue."
            ),
            "",
            "## Province/national totals",
            "",
            ("| Province | Process odt | Process PJ | Harvest gross odt | "
            "Harvest recoverable odt | Harvest recoverable PJ | RSYC ratio |"),
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    process = {
        row["province"]: row
        for row in province_rows
        if row["residue_type"] == "process_residue"
    }
    gross = {
        row["province"]: row
        for row in province_rows
        if row["residue_type"] == "harvest_residue_gross"
    }
    recoverable = {
        row["province"]: row
        for row in province_rows
        if row["residue_type"] == "harvest_residue_recoverable"
    }
    for province in PROVINCES:
        process_row = process[province]
        gross_row = gross[province]
        recoverable_row = recoverable[province]
        lines.append(
            f"| {province} | {_format_number(float(process_row['residue_odt']))} | "
            f"{_format_number(float(process_row['residue_pj']))} | "
            f"{_format_number(float(gross_row['residue_odt']))} | "
            f"{_format_number(float(recoverable_row['residue_odt']))} | "
            f"{_format_number(float(recoverable_row['residue_pj']))} | "
            f"{float(recoverable_row['rsyc_ratio']):.4f} |"
        )
    lines.extend(
        [
            "",
            (
                f"National process residue: "
                f"{_format_number(totals['process_residue_odt'])} odt / "
                f"{_format_number(totals['process_residue_pj'])} PJ. "
                f"National harvest residue: gross "
                f"{_format_number(totals['harvest_residue_gross_odt'])} odt / "
                f"{_format_number(totals['harvest_residue_gross_pj'])} PJ; "
                f"recoverable "
                f"{_format_number(totals['harvest_residue_recoverable_odt'])} odt / "
                f"{_format_number(totals['harvest_residue_recoverable_pj'])} PJ."
            ),
            "",
            "## RSYC comparison",
            "",
            (
                f"RSYC 119 Mm3 base recoverable residue is "
                f"{_format_number(totals['rsyc_base_119m_recoverable_odt'])} odt / "
                f"{_format_number(totals['rsyc_base_119m_recoverable_pj'])} PJ at "
                f"{rsyc['lhv_gj_per_odt']} GJ/odt. The model recoverable harvest "
                f"residue is "
                f"{_format_number(totals['model_recoverable_odt'])} odt / "
                f"{_format_number(totals['model_recoverable_pj'])} PJ, ratio "
                f"{rsyc['ratio']:.4f} (`{rsyc['status']}`)."
            ),
            "",
            (
                f"The documented RSYC 100 Mm3 base is "
                f"{_format_number(rsyc['rsyc_base_100m_recoverable_odt'])} odt / "
                f"{_format_number(rsyc['rsyc_base_100m_recoverable_pj'])} PJ. The "
                "familiar 129.6709 PJ figure is that 100 Mm3 reference, not a "
                "119 Mm3 value; the 119 Mm3 base is derived with the same "
                "province factors and the documented LHV."
            ),
            "",
            (
                "Process residue is not added to the RSYC comparison. A combined "
                f"process plus recoverable harvest stream is "
                f"{_format_number(totals['combined_process_plus_harvest_recoverable_odt'])} "
                f"odt / "
                f"{_format_number(totals['combined_process_plus_harvest_recoverable_pj'])} "
                "PJ, labelled `combined` and not RSYC-comparable."
            ),
            "",
            "## Proxy/concentration",
            "",
            f"- Facilities used: {int(concentration['facilities_used']):,}.",
            (
                f"- HHI of facility process residue: {concentration['hhi']:.6f}; "
                f"effective facility count: "
                f"{concentration['effective_facility_count']:.1f}."
            ),
            (
                f"- Top 1 share: {concentration['top1_share']:.2%}; top 5 share: "
                f"{concentration['top5_share']:.2%}."
            ),
            (
                f"- Published-capacity share of process residue: "
                f"{concentration['published_residue_share']:.2%}."
            ),
            (
                f"- Proxy-capacity share of process residue: "
                f"{concentration['proxy_residue_share']:.2%} "
                f"(scaled proxy: "
                f"{concentration['scaled_proxy_residue_share']:.2%})."
            ),
            (
                f"- National class fallback share of process residue: "
                f"{totals['fallback_residue_share']:.2%} "
                f"({_format_number(totals['fallback_residue_odt'])} odt)."
            ),
            (
                f"- Cross-border process residue (facility province differs "
                f"from origin): {totals['cross_border_residue_share']:.2%} "
                f"({_format_number(totals['cross_border_residue_odt'])} odt). "
                f"{ASSIGNMENT_LABEL.capitalize()} cross-border volume differs "
                "from the class-fallback cross-border residue because the "
                "fallback routes origin class streams to facilities in other "
                "provinces."
            ),
            "",
            "### Top facilities by process residue",
            "",
            ("| Rank | Facility | Class | Province | Residue odt | Share | "
            "Capacity status |"),
            "|---:|---|---|---|---:|---:|---|",
        ]
    )
    total = float(totals["process_residue_odt"])
    for rank, row in enumerate(
        sorted(
            facility_rows,
            key=lambda item: float(item["residue_odt"]),
            reverse=True,
        )[:20],
        start=1,
    ):
        share = float(row["residue_odt"]) / total if total > 0.0 else 0.0
        facility_label = row["facility_name"] or row["facility_id"]
        lines.append(
            f"| {rank} | {facility_label} | {row['facility_class']} | "
            f"{row['province']} | {_format_number(float(row['residue_odt']))} | "
            f"{share:.2%} | {row['capacity_status']} |"
        )
    lines.extend(
        [
            "",
            "## Labels and caveats",
            "",
            (
                "- `model_estimate=true`, `observed=false`, "
                "`policy_grade=false`; facility rows also carry "
                "`facility_assignment=true`."
            ),
            (
                "- Proxy capacity is a BC class-median screening fill, not an "
                "observed facility capacity. `proxy_harvest_scaled` records are "
                "proxy capacities scaled to the provincial 1.2x harvest floor."
            ),
            (
                "- Class matching forces each origin/class stream to assigned "
                "facilities of the same class. Provinces without a local assigned "
                "facility of a class use the documented national class fallback."
            ),
            (
                "- Facility feedstock is the product-chain class feedstock "
                f"allocated by {ASSIGNMENT_LABEL} assigned volume. It is not a "
                "re-measurement of facility infeed and may differ from the "
                "assigned_m3 weight."
            ),
            (
                "- Process residue is generated at facilities and is kept "
                "separate from provincial harvest residue. Harvest residue is "
                "never assigned to a facility."
            ),
            (
                "- Conversion uses the RSYC dry LHV from "
                f"`{rsyc['lhv_source']}`; no moisture, bark, or heat-rate "
                "adjustment is applied."
            ),
            (
                "- The RSYC comparison is a screening check, not a calibration. "
                "The 129.6709 PJ value is the documented 100 Mm3 RSYC base."
            ),
            (
                "- No transport, cost, revenue, capacity-expansion, tenure, AAC, "
                "or policy claim is made. No UI or public OSeCAN surface is "
                "changed."
            ),
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
                f"Run `.venv/bin/python -m {MODULE_PATH}` to reproduce the "
                "artifact and `.venv/bin/python -m pytest "
                f"{TEST_PATH} -q` to run the regression tests."
            ),
            "",
            "No policy/AAC claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the facility residue supply artifact and return its summary."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()

    rsvc = _parse_rsyc_basis()
    chains, product_chain_summary = _parse_product_chain()
    links, assignment_summary = _parse_assignment()

    allocations = _allocate(chains, links, rsvc)
    facility_totals, facility_meta = _facility_residue_totals(allocations)
    concentration = _concentration(
        allocations, facility_totals, facility_meta
    )
    province_rows = _build_province_rows(
        chains, allocations, facility_totals, facility_meta, rsvc
    )
    totals = _national_totals(
        chains, allocations, province_rows, concentration, rsvc
    )
    national_rows = _national_summary_rows(totals, rsvc)

    checks = _validate(
        chains=chains,
        allocations=allocations,
        province_rows=province_rows,
        national_rows=national_rows,
        totals=totals,
        concentration=concentration,
        product_chain_summary=product_chain_summary,
        assignment_summary=assignment_summary,
        rsvc=rsvc,
        deletion_clean=True,
    )
    failed = sum(1 for check in checks if check["status"] != "PASS")
    if failed:
        raise FacilityResidueSupplyError(f"{failed} validation checks failed")

    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            PRODUCT_CHAIN_DIR / "product_chain_by_province.csv",
            PRODUCT_CHAIN_DIR / "coefficients.csv",
            PRODUCT_CHAIN_DIR / "summary.json",
            ASSIGNMENT_DIR / "facility_assignment.csv",
            ASSIGNMENT_DIR / "facility_utilization.csv",
            ASSIGNMENT_DIR / "summary.json",
            RSYC_SUMMARY,
        )
    }
    scenario = assignment_summary["scenarios"][SCENARIO]
    assignment_v3 = {
        "scenario": SCENARIO,
        "assigned_m3": float(scenario["assigned_m3"]),
        "assigned_share": float(scenario.get("assigned_share", 0.0)),
        "unassigned_m3": float(scenario.get("unassigned_m3", 0.0)),
        "facilities_used": int(scenario["facilities_used"]),
        "proxy_share_of_assigned": float(
            scenario.get("proxy_share_of_assigned", 0.0)
        ),
        "cross_border_share": float(scenario.get("cross_border_share", 0.0)),
        "published_m3": float(scenario.get("published_m3", 0.0)),
        "proxy_m3": float(scenario.get("proxy_m3", 0.0)),
        "hhi": float(scenario.get("hhi", 0.0)),
        "max_facility_share": float(scenario.get("max_facility_share", 0.0)),
        "top5_share": float(scenario.get("top5_share", 0.0)),
        "scaled_proxy_records": int(
            assignment_summary.get("province_capacity_floor", {}).get(
                "scaled_proxy_records", 0
            )
        ),
        "scaled_provinces": list(
            assignment_summary.get("province_capacity_floor", {}).get(
                "scaled_provinces", []
            )
        ),
    }
    rsyc_comparison = {
        "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
        "lhv_source": rsvc.lhv_source,
        "base_recovery_fraction": RSYC_BASE_RECOVERY,
        "basis": (
            "RSYC province recoverable_odt_per_m3_base x provincial harvest; "
            "model gross harvest residue x 0.60"
        ),
        "rsyc_base_119m_recoverable_odt": totals[
            "rsyc_base_119m_recoverable_odt"
        ],
        "rsyc_base_119m_recoverable_pj": totals[
            "rsyc_base_119m_recoverable_pj"
        ],
        "rsyc_base_100m_recoverable_odt": totals[
            "rsyc_base_100m_recoverable_odt"
        ],
        "rsyc_base_100m_recoverable_pj": totals[
            "rsyc_base_100m_recoverable_pj"
        ],
        "model_recoverable_odt": totals["model_recoverable_odt"],
        "model_recoverable_pj": totals["model_recoverable_pj"],
        "ratio": totals["rsyc_ratio"],
        "status": totals["rsyc_status"],
        "process_residue_odt": totals["process_residue_odt"],
        "process_residue_pj": totals["process_residue_pj"],
        "combined_process_plus_harvest_recoverable_odt": totals[
            "combined_process_plus_harvest_recoverable_odt"
        ],
        "combined_process_plus_harvest_recoverable_pj": totals[
            "combined_process_plus_harvest_recoverable_pj"
        ],
        "note": (
            "The RSYC comparison applies to recoverable harvest residue only. "
            "Process residue is shown separately and is never added to the RSYC "
            "stream without the combined label. The 129.6709 PJ figure is the "
            "documented RSYC 100 Mm3 base; the 119 Mm3 base is derived with the "
            "same factors and the documented LHV."
        ),
    }
    province_totals: list[dict[str, Any]] = []
    for province, province_name in PROVINCES.items():
        process = next(
            row
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == "process_residue"
        )
        harvest_gross = next(
            row
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == "harvest_residue_gross"
        )
        harvest_recoverable = next(
            row
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == "harvest_residue_recoverable"
        )
        province_totals.append(
            {
                "province": province,
                "province_name": province_name,
                "process_residue_odt": process["residue_odt"],
                "process_residue_pj": process["residue_pj"],
                "origin_process_residue_odt": process[
                    "origin_process_residue_odt"
                ],
                "origin_process_residue_pj": process[
                    "origin_process_residue_pj"
                ],
                "harvest_residue_gross_odt": harvest_gross["residue_odt"],
                "harvest_residue_gross_pj": harvest_gross["residue_pj"],
                "harvest_residue_recoverable_odt": harvest_recoverable[
                    "residue_odt"
                ],
                "harvest_residue_recoverable_pj": harvest_recoverable[
                    "residue_pj"
                ],
                "rsyc_base_recoverable_odt": harvest_recoverable[
                    "rsyc_base_recoverable_odt"
                ],
                "rsyc_ratio": harvest_recoverable["rsyc_ratio"],
                "rsyc_status": harvest_recoverable["rsyc_status"],
                "facility_count": process["facility_count"],
                "facility_count_positive_residue": process[
                    "facility_count_positive_residue"
                ],
                "proxy_residue_share": process["proxy_residue_share"],
                "scaled_proxy_residue_share": process[
                    "scaled_proxy_residue_share"
                ],
                "evidence_tier": process["evidence_tier"],
            }
        )

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "scenario": SCENARIO,
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "facility_assignment": True,
        "ui_or_public_osecan_changed": False,
        "scope": (
            f"Facility-level process residue on {ASSIGNMENT_LABEL} "
            f"{SCENARIO}, plus separate provincial harvest residue "
            "and an RSYC 119 Mm3 comparison."
        ),
        "method": {
            "allocation": (
                "per origin province and class, proportional to "
                f"{ASSIGNMENT_LABEL} {SCENARIO} assigned volume among "
                "same-class facilities"
            ),
            "local_first": True,
            "fallback": (
                "national same-class assigned facilities when no province-local "
                "assigned facility of that class exists"
            ),
            "class_mixing": "prohibited",
            "conversion": (
                f"{PRODUCT_CHAIN_LABEL} effective province/class product and "
                "process-residue ratios applied to facility feedstock"
            ),
            "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
            "lhv_source": rsvc.lhv_source,
            "residue_density_odt_m3": RESIDUE_DENSITY_ODT_M3,
            "rsyc_base_recovery": RSYC_BASE_RECOVERY,
            "process_residue": "facility-level; odt and PJ",
            "harvest_residue": (
                "province-level gross/recoverable/unrecovered; not "
                "facility-assigned"
            ),
        },
        ASSIGNMENT_SUMMARY_KEY: assignment_v3,
        "lhv": {
            "value_gj_per_odt": rsvc.lhv_gj_per_odt,
            "source": "national RSYC residue factor summary",
            "source_path": rsvc.lhv_source,
        },
        "national_totals": totals,
        "rsyc_comparison": rsyc_comparison,
        "province_totals": province_totals,
        "concentration": concentration,
        "validation": {
            "checks": checks,
            "passed": failed == 0,
            "failed_check_count": failed,
        },
        "caveats": [
            "All values are model estimates; observed=false and policy_grade=false.",
            "Proxy facility capacity is a BC class-median fill, not an observed capacity; proxy and scaled-proxy shares are reported explicitly.",
            "Class matching prohibits class feedstock mixing and forces province class streams to assigned facilities of the same class.",
            "Provinces without a local assigned facility of a class use the national class fallback; this is not a transport or market model.",
            f"Facility feedstock is product-chain class feedstock allocated by {ASSIGNMENT_LABEL} weight, not a re-measured facility infeed.",
            "Process residue is facility-assigned and separate from provincial harvest residue; harvest residue is never facility-assigned.",
            "The RSYC comparison is a screening check on recoverable harvest residue, not a calibration; process residue is not added to it without the combined label.",
            "The 129.6709 PJ figure is the documented RSYC 100 Mm3 base, not the 119 Mm3 value.",
            "Conversion uses the documented dry LHV; no moisture, bark, or heat-rate adjustment is applied.",
            "No transport, cost, revenue, capacity-expansion, tenure, AAC, or policy claim is made.",
            "No UI or public OSeCAN surface is changed.",
        ],
        "inputs": inputs,
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    facility_fields = [
        "scenario",
        "province",
        "province_name",
        "origin_province",
        "origin_province_name",
        "facility_id",
        "facility_name",
        "facility_class",
        "feedstock_class",
        "cross_border",
        "allocation_scope",
        "allocation_basis",
        "assignment_weight_m3",
        "assignment_share",
        "assigned_m3",
        "capacity_status",
        "capacity_evidence",
        "floor_scale_factor",
        "capacity_m3_y",
        UTILIZATION_FIELD,
        "feedstock_m3",
        "share_of_origin_class_feedstock",
        "origin_class_reference_feedstock_m3",
        "product_quantity",
        "product_unit",
        "product_per_m3",
        "residue_odt",
        "residue_pj",
        "residue_type",
        "residue_odt_per_m3",
        "lhv_gj_per_odt",
        "facility_process_residue_odt",
        "facility_share_of_process_residue",
        "coefficient_tier",
        "evidence_tier",
        "conversion_basis",
        "evidence_label",
        "model_estimate",
        "observed",
        "policy_grade",
        "facility_assignment",
        "notes",
    ]
    total_process_residue = float(totals["process_residue_odt"])
    facility_rows: list[dict[str, Any]] = []
    for allocation in allocations:
        facility_total = facility_totals[allocation.facility_id]
        reference_feedstock = chains[
            allocation.origin_province
        ].class_feedstock_m3[allocation.facility_class]
        evidence_tier = (
            f"{allocation.coefficient_tier}+{allocation.capacity_evidence}"
        )
        facility_rows.append(
            {
                "scenario": SCENARIO,
                "province": allocation.facility_province,
                "province_name": PROVINCES[allocation.facility_province],
                "origin_province": allocation.origin_province,
                "origin_province_name": PROVINCES[allocation.origin_province],
                "facility_id": allocation.facility_id,
                "facility_name": allocation.facility_name,
                "facility_class": allocation.facility_class,
                "feedstock_class": allocation.facility_class,
                "cross_border": LABEL_TRUE if allocation.cross_border else LABEL_FALSE,
                "allocation_scope": allocation.allocation_scope,
                "allocation_basis": ALLOCATION_BASIS,
                "assignment_weight_m3": allocation.assignment_weight_m3,
                "assignment_share": allocation.assignment_share,
                "assigned_m3": allocation.assigned_m3,
                "capacity_status": allocation.capacity_status,
                "capacity_evidence": allocation.capacity_evidence,
                "floor_scale_factor": allocation.floor_scale_factor,
                "capacity_m3_y": allocation.capacity_m3_y,
                UTILIZATION_FIELD: allocation.utilization,
                "feedstock_m3": allocation.feedstock_m3,
                "share_of_origin_class_feedstock": allocation.assignment_share,
                "origin_class_reference_feedstock_m3": reference_feedstock,
                "product_quantity": allocation.product_quantity,
                "product_unit": allocation.product_unit,
                "product_per_m3": allocation.product_per_m3,
                "residue_odt": allocation.residue_odt,
                "residue_pj": allocation.residue_pj,
                "residue_type": "process_residue",
                "residue_odt_per_m3": allocation.residue_odt_per_m3,
                "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
                "facility_process_residue_odt": facility_total,
                "facility_share_of_process_residue": (
                    facility_total / total_process_residue
                    if total_process_residue > 0.0
                    else 0.0
                ),
                "coefficient_tier": allocation.coefficient_tier,
                "evidence_tier": evidence_tier,
                "conversion_basis": (
                    "product_chain_effective_province_class_ratio"
                ),
                "evidence_label": EVIDENCE_LABEL,
                "model_estimate": LABEL_TRUE,
                "observed": LABEL_FALSE,
                "policy_grade": LABEL_FALSE,
                "facility_assignment": LABEL_TRUE,
                "notes": (
                    "Class-matched feedstock; process residue at the facility; "
                    "provincial harvest residue is not included."
                ),
            }
        )

    province_fields = [
        "province",
        "province_name",
        "residue_type",
        "residue_odt",
        "residue_pj",
        "lhv_gj_per_odt",
        "residue_province_basis",
        "facility_assigned",
        "facility_count",
        "facility_count_positive_residue",
        "origin_process_residue_odt",
        "origin_process_residue_pj",
        "published_residue_share",
        "proxy_residue_share",
        "scaled_proxy_residue_share",
        "top1_facility_residue_share",
        "hhi_facility_residue",
        "evidence_tier",
        "capacity_status_summary",
        "rsyc_comparable",
        "rsyc_base_recoverable_odt",
        "model_recoverable_odt_base",
        "rsyc_ratio",
        "rsyc_status",
        "model_estimate",
        "observed",
        "policy_grade",
        "notes",
    ]
    national_fields = [
        "metric",
        "residue_type",
        "unit",
        "value",
        "rsyc_comparable",
        "notes",
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        target_dir / "facility_residue_supply.csv",
        facility_rows,
        facility_fields,
    )
    _write_csv(
        target_dir / "province_residue_supply.csv",
        province_rows,
        province_fields,
    )
    _write_csv(
        target_dir / "national_residue_summary.csv",
        national_rows,
        national_fields,
    )
    _write_json(target_dir / "summary.json", summary)
    (target_dir / REPORT_NAME).write_text(
        _report(summary, facility_rows, province_rows), encoding="utf-8"
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
        "scenario": SCENARIO,
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "facility_assignment": True,
            "ui_or_public_osecan_changed": False,
        },
        "inputs": inputs,
        "outputs": outputs,
        "record_counts": {
            "facility_rows": len(facility_rows),
            "province_rows": len(province_rows),
            "national_summary_rows": len(national_rows),
            "facilities_used": len(facility_totals),
            "facilities_with_positive_residue": int(
                totals["facilities_with_positive_residue"]
            ),
        },
        "validation": summary["validation"],
        "rsyc_comparison": rsyc_comparison,
        "reproduce": summary["reproduce"],
    }
    _write_json(target_dir / "manifest.json", manifest)
    summary["outputs"] = outputs
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "scenario": summary["scenario"],
                "process_residue_odt": summary["national_totals"][
                    "process_residue_odt"
                ],
                "process_residue_pj": summary["national_totals"][
                    "process_residue_pj"
                ],
                "harvest_residue_recoverable_odt": summary["national_totals"][
                    "harvest_residue_recoverable_odt"
                ],
                "rsyc_ratio": summary["rsyc_comparison"]["ratio"],
                "proxy_residue_share": summary["concentration"][
                    "proxy_residue_share"
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
