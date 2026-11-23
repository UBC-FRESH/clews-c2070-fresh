"""Build multi-scenario national facility product/residue chain v1.

The artifact runs the assignment-v4 allocation rule once for each documented
provincial harvest ceiling:

* ``100m`` -- ``provincial_allocation/100m.csv``
* ``119m`` -- ``provincial_allocation/119m.csv``
* ``150m`` -- ``provincial_allocation/150m.csv``

Every ceiling uses the same capacity-v2 published-plus-proxy facility set, the
v2 local-first allocation rule, the documented 500 km cross-border penalty, and
finite capacity only.  There is no unbounded sink.

Product and residue quantities are built by applying the product-chain-v1
effective province/class coefficients to the assigned class feedstock.  The
reference product chain is at 119 Mm3; for another ceiling its province rows
are scaled by that province's harvest ratio.  This preserves every
province/class product-per-m3 and residue-per-m3 coefficient.  Process residue
is facility-generated and is kept separate from provincial harvest residue.

The 150 Mm3 documented ceiling in this artifact is **not** the 1.0 or
1.2 m3/ha/yr operable-proxy intensity screen.  Both intensity screens are
infeasible at 150 Mm3 and are reported separately, without changing or
re-labelling the documented-ceiling scenario.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No
legal, AAC, policy, or investment claim is made.  No UI or public OSeCAN
surface is changed.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v2 as v2,
)
from workflows.national.facilities import (
    national_facility_assignment_v3 as v3,
)
from workflows.national.facilities import (
    national_facility_assignment_v4 as v4,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
    ProvinceOffer,
    load_province_centroids,
)
from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v1"
)
CAPACITY_CSV = v4.CAPACITY_CSV
INVENTORY_CSV = v4.INVENTORY_CSV
BOUNDARY_GEOJSON = v4.BOUNDARY_GEOJSON
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v1"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v1"
RSYC_SUMMARY = (
    DATA_ROOT
    / "derived/national/clews_supply/rsyc_backbone/"
    "national_residue_factor_summary.json"
)
ALLOCATION_DIR = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation"
)
HARVEST_CASES = (
    ("100m", 100_000_000.0),
    ("119m", 118_977_339.1),
    ("150m", 150_000_000.0),
)
SCENARIO_ORDER = tuple(case[0] for case in HARVEST_CASES)
PRIMARY_SCENARIO = "119m"
CAPACITY_CASE = "published_plus_proxy"
INTENSITY_CASES = ("1.0", "1.2")

ARTIFACT = "national_facility_chain_scenarios_v1"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V1"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V1.md"
CAPACITY_LABEL = "inventory v1 / capacity v2"
ASSIGNMENT_LABEL = "assignment-v4"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios"
TEST_PATH = "tests/test_national_facility_chain_scenarios.py"
EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
FACILITY_ASSIGNMENT = True

CLASS_ORDER = v2.CLASS_ORDER[:5]
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
PRODUCT_SUMMARY_FIELDS = {
    "SAW": "sawlog_product_m3",
    "PULP": "pulp_product_odt",
    "PANEL": "panel_product_m3",
    "PELLET": "pellet_product_odt",
    "BIO": "bio_product_mwh",
}
RESIDUE_DENSITY_ODT_M3 = 0.45
PJ_GJ = 1_000_000.0
HARVEST_TOTAL_TOLERANCE_M3 = 1.0
VOLUME_EPSILON_M3 = v2.VOLUME_EPSILON_M3
REL_TOL = 1e-8
ABS_TOL = 1e-6

SCENARIO_ASSIGNMENT_FIELDS = [
    "scenario",
    "harvest_case_m3",
    "capacity_case",
    "allocation_order",
    "origin_province",
    "province_offer_m3",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_phase",
    "capacity_status",
    "capacity_evidence",
    "capacity_m3_y",
    "floor_scale_factor",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "distance_km",
    "road_distance_km",
    "cross_border_penalty_km",
    "rank_distance_km",
    "source_id",
    "source_url",
    "capacity_source_id",
    "capacity_source_url",
    "evidence_tier",
    "evidence_label",
    "observed",
    "policy_grade",
]
SCENARIO_PRODUCT_RESIDUE_FIELDS = [
    "scenario",
    "harvest_case_m3",
    "province",
    "province_name",
    "record_type",
    "facility_class",
    "feedstock_m3",
    "product_quantity",
    "product_unit",
    "process_residue_odt",
    "process_residue_m3",
    "process_residue_pj",
    "harvest_residue_gross_m3",
    "harvest_residue_gross_odt",
    "harvest_residue_gross_pj",
    "harvest_residue_recoverable_m3",
    "harvest_residue_recoverable_odt",
    "harvest_residue_recoverable_pj",
    "harvest_residue_unrecovered_m3",
    "harvest_residue_unrecovered_odt",
    "harvest_residue_unrecovered_pj",
    "facility_assigned",
    "allocation_scope",
    "conversion_basis",
    "coefficient_tier",
    "evidence_label",
    "observed",
    "policy_grade",
]
SCENARIO_SUMMARY_FIELDS = [
    "scenario",
    "harvest_case_m3",
    "harvest_m3",
    "capacity_case",
    "assigned_m3",
    "assigned_share",
    "unassigned_m3",
    "overflow_m3",
    "overflow_flag",
    "eligible_capacity_m3_y",
    "published_capacity_m3_y",
    "proxy_capacity_m3_y",
    "capacity_utilization",
    "global_capacity_binding",
    "local_capacity_binding_provinces",
    "local_capacity_binding_m3",
    "published_m3",
    "proxy_m3",
    "proxy_share_of_assigned",
    "cross_border_m3",
    "cross_border_share",
    "facilities_used",
    "max_facility_m3",
    "max_facility_share",
    "top5_share",
    "hhi",
    "facility_feedstock_m3",
    "sawlog_feedstock_m3",
    "pulp_feedstock_m3",
    "panel_feedstock_m3",
    "pellet_feedstock_m3",
    "bio_feedstock_m3",
    "sawlog_product_m3",
    "pulp_product_odt",
    "panel_product_m3",
    "pellet_product_odt",
    "bio_product_mwh",
    "process_residue_odt",
    "process_residue_m3",
    "process_residue_pj",
    "harvest_residue_gross_odt",
    "harvest_residue_gross_pj",
    "harvest_residue_recoverable_odt",
    "harvest_residue_recoverable_pj",
    "harvest_residue_unrecovered_odt",
    "harvest_residue_unrecovered_pj",
    "total_residue_gross_odt",
    "total_residue_gross_pj",
    "total_residue_recoverable_odt",
    "total_residue_recoverable_pj",
    "national_class_fallback_pools",
    "lhv_gj_per_odt",
    "evidence_label",
    "observed",
    "policy_grade",
]
SCENARIO_COMPARISON_FIELDS = [
    "scenario",
    "harvest_m3",
    "assigned_m3",
    "unassigned_m3",
    "capacity_utilization",
    "cross_border_m3",
    "cross_border_share",
    "proxy_share_of_assigned",
    "facilities_used",
    "max_facility_share",
    "hhi",
    "total_residue_gross_pj",
    "total_residue_recoverable_pj",
    "process_residue_pj",
    "harvest_residue_gross_pj",
    "harvest_residue_recoverable_pj",
    "sawlog_product_m3",
    "pulp_product_odt",
    "panel_product_m3",
    "pellet_product_odt",
    "bio_product_mwh",
    "delta_harvest_m3_vs_119m",
    "delta_assigned_m3_vs_119m",
    "delta_capacity_utilization_vs_119m",
    "delta_total_residue_gross_pj_vs_119m",
    "delta_total_residue_recoverable_pj_vs_119m",
    "delta_process_residue_pj_vs_119m",
    "delta_sawlog_product_m3_vs_119m",
    "delta_pulp_product_odt_vs_119m",
    "delta_panel_product_m3_vs_119m",
    "delta_pellet_product_odt_vs_119m",
    "delta_bio_product_mwh_vs_119m",
    "evidence_label",
    "observed",
    "policy_grade",
]


class ScenarioChainError(RuntimeError):
    """Raised when a scenario input or invariant cannot be trusted."""


@dataclass(frozen=True)
class HarvestCase:
    """One documented provincial harvest ceiling."""

    scenario: str
    expected_m3: float
    path: Path


@dataclass(frozen=True)
class ReferenceChain:
    """Parsed 119 Mm3 product-chain province row."""

    province: str
    province_name: str
    harvest_m3: float
    primary_feedstock_m3: float
    residue_feedstock_m3: float
    total_feedstock_m3: float
    class_feedstock_m3: Mapping[str, float]
    class_product: Mapping[str, float]
    class_residue_odt: Mapping[str, float]
    harvest_residue_gross_m3: float
    harvest_residue_gross_odt: float
    harvest_residue_recoverable_m3: float
    harvest_residue_recoverable_odt: float
    harvest_residue_unrecovered_m3: float
    harvest_residue_unrecovered_odt: float
    coefficient_tier: str


@dataclass(frozen=True)
class ScenarioChain:
    """Product-chain province row scaled to one harvest ceiling."""

    province: str
    province_name: str
    harvest_m3: float
    class_feedstock_m3: Mapping[str, float]
    class_product: Mapping[str, float]
    class_residue_odt: Mapping[str, float]
    harvest_residue_gross_m3: float
    harvest_residue_gross_odt: float
    harvest_residue_recoverable_m3: float
    harvest_residue_recoverable_odt: float
    harvest_residue_unrecovered_m3: float
    harvest_residue_unrecovered_odt: float
    coefficient_tier: str


@dataclass(frozen=True)
class AssignmentLink:
    """One assignment-v4 row used as a class-feedstock weight."""

    origin_province: str
    facility_id: str
    facility_class: str
    facility_province: str
    cross_border: bool
    allocation_phase: str
    capacity_status: str
    capacity_evidence: str
    capacity_m3_y: float
    assigned_m3: float
    utilization: float


@dataclass(frozen=True)
class FeedstockAllocation:
    """Class feedstock, product, and process residue at one facility."""

    origin_province: str
    facility_class: str
    facility_id: str
    allocation_scope: str
    feedstock_m3: float
    product_quantity: float
    product_unit: str
    process_residue_odt: float
    coefficient_tier: str


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(
    path: Path, required: Collection[str], label: str
) -> list[dict[str, str]]:
    if not path.is_file():
        raise ScenarioChainError(f"{label} does not exist: {path}")
    rows = v2._read_csv(path)
    if not rows:
        raise ScenarioChainError(f"{label} is empty: {path}")
    v2._require_columns(rows, set(required), path)
    return rows


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ScenarioChainError(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed):
        raise ScenarioChainError(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0:
        raise ScenarioChainError(f"{label} is negative: {parsed!r}")
    return parsed


def _require_close(left: float, right: float, label: str) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > REL_TOL * scale:
        raise ScenarioChainError(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(relative {abs(left - right) / scale:.3e})"
        )


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _format(value: float | None, digits: int = 9) -> str:
    return v2._format_float(value, digits)


def _git_deletions(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ScenarioChainError(
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
        raise ScenarioChainError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def harvest_cases() -> tuple[HarvestCase, ...]:
    """Return the documented-ceiling cases and fail if a CSV is missing."""

    cases: list[HarvestCase] = []
    for scenario, expected_m3 in HARVEST_CASES:
        path = ALLOCATION_DIR / f"{scenario}.csv"
        if not path.is_file():
            raise ScenarioChainError(f"harvest case is missing: {path}")
        cases.append(
            HarvestCase(
                scenario=scenario,
                expected_m3=expected_m3,
                path=path,
            )
        )
    return tuple(cases)


def read_harvest_case(case: HarvestCase) -> dict[str, float]:
    """Parse one documented ceiling and validate its national total."""

    rows = _read_rows(
        case.path,
        {"province", "harvest_m3"},
        f"harvest case {case.scenario}",
    )
    harvest: dict[str, float] = {}
    for position, row in enumerate(rows, start=2):
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise ScenarioChainError(
                f"{case.path}:{position}: unexpected province {province!r}"
            )
        if province in harvest:
            raise ScenarioChainError(
                f"{case.path}:{position}: duplicate province {province}"
            )
        harvest[province] = _finite_nonnegative(
            row["harvest_m3"],
            f"{case.path}:{position} harvest_m3",
        )
    missing = set(PROVINCES) - set(harvest)
    if missing:
        raise ScenarioChainError(
            f"{case.path}: missing provinces {sorted(missing)}"
        )
    total = math.fsum(harvest.values())
    if abs(total - case.expected_m3) > HARVEST_TOTAL_TOLERANCE_M3:
        raise ScenarioChainError(
            f"{case.path}: national total {total:,.3f} m3 does not match the "
            f"documented {case.scenario} ceiling ({case.expected_m3:,.1f} m3)"
        )
    return harvest


def read_offers(
    harvest: Mapping[str, float],
    boundary_path: Path = BOUNDARY_GEOJSON,
) -> tuple[tuple[ProvinceOffer, ...], dict[str, str]]:
    """Build one ungraded offer per province from a validated harvest case."""

    centroids, methods = load_province_centroids(boundary_path)
    offers = tuple(
        ProvinceOffer(
            province=province,
            volume_m3=harvest[province],
            location=centroids[province],
        )
        for province in PROVINCES
    )
    return offers, methods


def read_reference_chains() -> tuple[dict[str, ReferenceChain], dict[str, Any]]:
    """Parse the 119 Mm3 product-chain-v1 province table."""

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
        "harvest_residue_gross_m3",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_m3",
        "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_m3",
        "harvest_residue_unrecovered_odt",
        "coefficient_tier",
    ]
    rows = _read_rows(
        PRODUCT_CHAIN_CSV,
        required,
        "product-chain province table",
    )
    if not PRODUCT_CHAIN_SUMMARY.is_file():
        raise ScenarioChainError(
            f"product-chain summary is missing: {PRODUCT_CHAIN_SUMMARY}"
        )
    summary = json.loads(PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8"))
    if summary.get("artifact") != PRODUCT_CHAIN_ARTIFACT:
        raise ScenarioChainError(
            f"unexpected product-chain artifact: {summary.get('artifact')!r}"
        )
    if (
        summary.get("model_estimate") is not True
        or summary.get("observed") is not False
        or summary.get("policy_grade") is not False
    ):
        raise ScenarioChainError("product-chain labels are not screening labels")

    chains: dict[str, ReferenceChain] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise ScenarioChainError(
                f"unexpected product-chain province {province!r}"
            )
        if province in chains:
            raise ScenarioChainError(
                f"duplicate product-chain province {province}"
            )
        harvest = _finite_nonnegative(
            row["harvest_m3"], f"{province} harvest_m3"
        )
        primary = _finite_nonnegative(
            row["primary_feedstock_m3"], f"{province} primary_feedstock_m3"
        )
        residue_feedstock = _finite_nonnegative(
            row["residue_feedstock_m3"],
            f"{province} residue_feedstock_m3",
        )
        total_feedstock = _finite_nonnegative(
            row["total_feedstock_m3"], f"{province} total_feedstock_m3"
        )
        _require_close(
            primary + residue_feedstock,
            total_feedstock,
            f"{province} primary plus residue feedstock",
        )
        _require_close(primary, harvest, f"{province} primary versus harvest")
        class_feedstock = {
            facility_class: _finite_nonnegative(
                row[CLASS_FEEDSTOCK_COLUMN[facility_class]],
                f"{province}/{facility_class} feedstock",
            )
            for facility_class in CLASS_ORDER
        }
        _require_close(
            math.fsum(class_feedstock.values()),
            total_feedstock,
            f"{province} class feedstock conservation",
        )
        coefficient_tier = row["coefficient_tier"].strip()
        if coefficient_tier not in {
            "bc_anchor_species_group",
            "donor_coefficient",
            "measured_bucking_pilot",
        }:
            raise ScenarioChainError(
                f"{province}: unexpected coefficient tier "
                f"{coefficient_tier!r}"
            )
        _require_close(
            _finite_nonnegative(
                row["harvest_residue_recoverable_odt"],
                f"{province} harvest_residue_recoverable_odt",
            )
            + _finite_nonnegative(
                row["harvest_residue_unrecovered_odt"],
                f"{province} harvest_residue_unrecovered_odt",
            ),
            _finite_nonnegative(
                row["harvest_residue_gross_odt"],
                f"{province} harvest_residue_gross_odt",
            ),
            f"{province} harvest residue split",
        )
        chains[province] = ReferenceChain(
            province=province,
            province_name=row["province_name"].strip(),
            harvest_m3=harvest,
            primary_feedstock_m3=primary,
            residue_feedstock_m3=residue_feedstock,
            total_feedstock_m3=total_feedstock,
            class_feedstock_m3=class_feedstock,
            class_product={
                facility_class: _finite_nonnegative(
                    row[CLASS_PRODUCT_COLUMN[facility_class]],
                    f"{province}/{facility_class} product",
                )
                for facility_class in CLASS_ORDER
            },
            class_residue_odt={
                facility_class: _finite_nonnegative(
                    row[CLASS_RESIDUE_COLUMN[facility_class]],
                    f"{province}/{facility_class} residue",
                )
                for facility_class in CLASS_ORDER
            },
            harvest_residue_gross_m3=_finite_nonnegative(
                row["harvest_residue_gross_m3"],
                f"{province} harvest_residue_gross_m3",
            ),
            harvest_residue_gross_odt=_finite_nonnegative(
                row["harvest_residue_gross_odt"],
                f"{province} harvest_residue_gross_odt",
            ),
            harvest_residue_recoverable_m3=_finite_nonnegative(
                row["harvest_residue_recoverable_m3"],
                f"{province} harvest_residue_recoverable_m3",
            ),
            harvest_residue_recoverable_odt=_finite_nonnegative(
                row["harvest_residue_recoverable_odt"],
                f"{province} harvest_residue_recoverable_odt",
            ),
            harvest_residue_unrecovered_m3=_finite_nonnegative(
                row["harvest_residue_unrecovered_m3"],
                f"{province} harvest_residue_unrecovered_m3",
            ),
            harvest_residue_unrecovered_odt=_finite_nonnegative(
                row["harvest_residue_unrecovered_odt"],
                f"{province} harvest_residue_unrecovered_odt",
            ),
            coefficient_tier=coefficient_tier,
        )
    missing = set(PROVINCES) - set(chains)
    extra = set(chains) - set(PROVINCES)
    if missing or extra:
        raise ScenarioChainError(
            "product-chain province set mismatch: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return chains, summary


def scale_chain(reference: ReferenceChain, harvest_m3: float) -> ScenarioChain:
    """Scale a reference province row while preserving every coefficient."""

    if reference.harvest_m3 <= 0.0:
        if harvest_m3 > VOLUME_EPSILON_M3:
            raise ScenarioChainError(
                f"{reference.province}: positive scenario harvest with zero "
                "reference harvest"
            )
        ratio = 0.0
    else:
        ratio = harvest_m3 / reference.harvest_m3
    return ScenarioChain(
        province=reference.province,
        province_name=reference.province_name,
        harvest_m3=harvest_m3,
        class_feedstock_m3={
            facility_class: quantity * ratio
            for facility_class, quantity in reference.class_feedstock_m3.items()
        },
        class_product={
            facility_class: quantity * ratio
            for facility_class, quantity in reference.class_product.items()
        },
        class_residue_odt={
            facility_class: quantity * ratio
            for facility_class, quantity in reference.class_residue_odt.items()
        },
        harvest_residue_gross_m3=reference.harvest_residue_gross_m3 * ratio,
        harvest_residue_gross_odt=reference.harvest_residue_gross_odt * ratio,
        harvest_residue_recoverable_m3=(
            reference.harvest_residue_recoverable_m3 * ratio
        ),
        harvest_residue_recoverable_odt=(
            reference.harvest_residue_recoverable_odt * ratio
        ),
        harvest_residue_unrecovered_m3=(
            reference.harvest_residue_unrecovered_m3 * ratio
        ),
        harvest_residue_unrecovered_odt=(
            reference.harvest_residue_unrecovered_odt * ratio
        ),
        coefficient_tier=reference.coefficient_tier,
    )


def build_scenario_chains(
    references: Mapping[str, ReferenceChain],
    harvest: Mapping[str, float],
) -> dict[str, ScenarioChain]:
    """Build one coefficient-preserving chain per province for a scenario."""

    chains: dict[str, ScenarioChain] = {}
    for province in PROVINCES:
        scaled = scale_chain(references[province], harvest[province])
        expected_feedstock = math.fsum(scaled.class_feedstock_m3.values())
        _require_close(
            expected_feedstock,
            references[province].total_feedstock_m3
            * (
                harvest[province] / references[province].harvest_m3
                if references[province].harvest_m3 > 0.0
                else 1.0
            ),
            f"{province} scaled class feedstock conservation",
        )
        chains[province] = scaled
    return chains


def read_lhv() -> tuple[float, str]:
    """Read the documented residue LHV used by the residue artifact."""

    if not RSYC_SUMMARY.is_file():
        raise ScenarioChainError(f"RSYC summary is missing: {RSYC_SUMMARY}")
    payload = json.loads(RSYC_SUMMARY.read_text(encoding="utf-8"))
    lhv = _finite_nonnegative(
        payload.get("definitions", {}).get("lhv_gj_per_odt"),
        "RSYC lhv_gj_per_odt",
    )
    if lhv <= 0.0:
        raise ScenarioChainError("RSYC LHV must be positive")
    return lhv, str(RSYC_SUMMARY.relative_to(REPO_ROOT))


def read_intensity_note() -> dict[str, Any]:
    """Read 1.0/1.2 intensity status without changing the 150m case."""

    cases: list[dict[str, Any]] = []
    for intensity in INTENSITY_CASES:
        path = (
            ALLOCATION_DIR
            / f"intensity_{intensity}"
            / "provincial_allocation_summary.json"
        )
        if not path.is_file():
            raise ScenarioChainError(f"intensity summary is missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        case = payload.get("cases", {}).get("150m")
        if not isinstance(case, dict):
            raise ScenarioChainError(
                f"{path}: no 150m case in intensity summary"
            )
        status = str(case.get("status", "")).strip()
        if status not in {"optimal", "infeasible"}:
            raise ScenarioChainError(
                f"{path}: unexpected 150m status {status!r}"
            )
        cases.append(
            {
                "intensity_m3_per_ha_yr": float(intensity),
                "status": status,
                "feasible": bool(case.get("feasible")),
                "national_total_m3": float(
                    case.get("national_total_m3", 150_000_000.0)
                ),
                "deficit_m3": float(case.get("deficit_m3", 0.0) or 0.0),
                "capacity_m3_y": float(
                    case.get("capacity_m3", payload.get("capacity_m3", 0.0))
                ),
                "source": str(path.relative_to(REPO_ROOT)),
            }
        )
    return {
        "documented_ceiling_scenario": "150m",
        "documented_ceiling_source": str(
            (ALLOCATION_DIR / "150m.csv").relative_to(REPO_ROOT)
        ),
        "documented_ceiling_note": (
            "The 150m scenario uses the documented provincial ceiling table. "
            "It is not an operable-proxy intensity screen."
        ),
        "intensity_screens": cases,
        "conflation_warning": (
            "The 1.0 and 1.2 m3/ha/yr operable-proxy screens are infeasible at "
            "150 Mm3. They are separate sensitivity results and must not be "
            "used to relabel or reject the documented 150m ceiling case."
        ),
    }


def eligible_facilities(facilities: Sequence[v3.Facility]) -> list[v3.Facility]:
    """Return facilities that are assignable under published_plus_proxy."""

    return [
        facility
        for facility in facilities
        if facility.facility_class in v2.RELEVANT_CLASSES
        and facility.is_operating
        and facility.location is not None
        and facility.capacity is not None
    ]


def capacity_by_province(
    facilities: Sequence[v3.Facility],
) -> dict[str, float]:
    capacity = {province: 0.0 for province in PROVINCES}
    for facility in eligible_facilities(facilities):
        capacity[facility.province] += float(facility.capacity_m3_y or 0.0)
    return capacity


def capacity_v2_basis(facilities: Sequence[v3.Facility]) -> dict[str, Any]:
    """Report capacity-v2 totals before and after the assignment screen."""

    relevant = [
        facility
        for facility in facilities
        if facility.facility_class in v2.RELEVANT_CLASSES
        and facility.capacity is not None
    ]
    published = [
        facility
        for facility in relevant
        if facility.capacity is not None
        and facility.capacity.status == v3.PUBLISHED
    ]
    proxies = [
        facility
        for facility in relevant
        if facility.capacity is not None
        and facility.capacity.status == v3.PROXY
    ]
    unlocated = [
        facility for facility in relevant if facility.location is None
    ]
    non_operating = [
        facility for facility in relevant if not facility.is_operating
    ]
    eligible = eligible_facilities(facilities)
    return {
        "records_total": len(relevant),
        "published_records": len(published),
        "proxy_records": len(proxies),
        "not_published_records": sum(
            1 for facility in facilities if facility.capacity is None
        ),
        "published_capacity_m3_y": math.fsum(
            float(facility.capacity_m3_y or 0.0) for facility in published
        ),
        "proxy_capacity_m3_y": math.fsum(
            float(facility.capacity_m3_y or 0.0) for facility in proxies
        ),
        "total_capacity_m3_y": math.fsum(
            float(facility.capacity_m3_y or 0.0) for facility in relevant
        ),
        "unlocated_records": len(unlocated),
        "unlocated_capacity_m3_y": math.fsum(
            float(facility.capacity_m3_y or 0.0) for facility in unlocated
        ),
        "non_operating_records": len(non_operating),
        "eligible_records": len(eligible),
        "eligible_capacity_m3_y": math.fsum(
            float(facility.capacity_m3_y or 0.0) for facility in eligible
        ),
    }


def _assignment_links(
    result: v2.ScenarioResult,
) -> list[AssignmentLink]:
    links: list[AssignmentLink] = []
    for row in result.assignments:
        capacity = _finite_nonnegative(
            row["capacity_m3_y"], f"{row['facility_id']} capacity"
        )
        assigned = _finite_nonnegative(
            row["assigned_m3"], f"{row['facility_id']} assigned"
        )
        if capacity <= 0.0:
            raise ScenarioChainError(
                f"{row['facility_id']}: assignment has no finite capacity"
            )
        if assigned > capacity + VOLUME_EPSILON_M3:
            raise ScenarioChainError(
                f"{row['facility_id']}: assigned above capacity"
            )
        links.append(
            AssignmentLink(
                origin_province=row["province"],
                facility_id=row["facility_id"],
                facility_class=row["facility_class"],
                facility_province=row["facility_province"],
                cross_border=row["cross_border"] == "true",
                allocation_phase=row["allocation_phase"],
                capacity_status=row["capacity_status"],
                capacity_evidence=row["capacity_evidence"],
                capacity_m3_y=capacity,
                assigned_m3=assigned,
                utilization=float(row["utilization"]),
            )
        )
    return links


def _allocation_pool(
    province: str,
    facility_class: str,
    local: Sequence[AssignmentLink],
    national: Sequence[AssignmentLink],
) -> tuple[list[AssignmentLink], str]:
    if local:
        return list(local), "province_assignment"
    if not national:
        raise ScenarioChainError(
            f"no assigned {facility_class} facilities exist nationally for "
            f"{province} class feedstock"
        )
    return list(national), "national_class_fallback"


def allocate_class_feedstock(
    chains: Mapping[str, ScenarioChain],
    links: Sequence[AssignmentLink],
) -> tuple[list[FeedstockAllocation], int]:
    """Allocate class feedstock to same-class facilities by assigned weight."""

    local_by_origin_class: dict[
        tuple[str, str], list[AssignmentLink]
    ] = defaultdict(list)
    national_by_class: dict[str, list[AssignmentLink]] = defaultdict(list)
    for link in links:
        if link.facility_class not in CLASS_ORDER:
            raise ScenarioChainError(
                f"{link.facility_id}: unexpected facility class "
                f"{link.facility_class!r}"
            )
        local_by_origin_class[
            (link.origin_province, link.facility_class)
        ].append(link)
        national_by_class[link.facility_class].append(link)

    allocations: list[FeedstockAllocation] = []
    fallback_pools = 0
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
            if scope == "national_class_fallback":
                fallback_pools += 1
            total_weight = math.fsum(link.assigned_m3 for link in pool)
            if total_weight <= 0.0:
                raise ScenarioChainError(
                    f"{province}/{facility_class}: allocation weight is zero"
                )
            product_reference = chain.class_product[facility_class]
            residue_reference = chain.class_residue_odt[facility_class]
            product_per_m3 = product_reference / feedstock
            residue_odt_per_m3 = residue_reference / feedstock
            group: list[FeedstockAllocation] = []
            for link in pool:
                if link.facility_class != facility_class:
                    raise ScenarioChainError(
                        f"{link.facility_id}: class mixing is not permitted"
                    )
                share = link.assigned_m3 / total_weight
                volume = feedstock * share
                product = volume * product_per_m3
                residue_odt = volume * residue_odt_per_m3
                if not all(
                    math.isfinite(value) and value >= 0.0
                    for value in (volume, product, residue_odt)
                ):
                    raise ScenarioChainError(
                        f"{province}/{facility_class}/{link.facility_id}: "
                        "invalid allocation quantity"
                    )
                group.append(
                    FeedstockAllocation(
                        origin_province=province,
                        facility_class=facility_class,
                        facility_id=link.facility_id,
                        allocation_scope=scope,
                        feedstock_m3=volume,
                        product_quantity=product,
                        product_unit=CLASS_PRODUCT_UNIT[facility_class],
                        process_residue_odt=residue_odt,
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
                math.fsum(item.process_residue_odt for item in group),
                residue_reference,
                f"{province}/{facility_class} facility residue conservation",
            )
            allocations.extend(group)
    return allocations, fallback_pools


def _scenario_assignment_rows(
    case: HarvestCase,
    result: v2.ScenarioResult,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in result.assignments:
        rows.append(
            {
                "scenario": case.scenario,
                "harvest_case_m3": _format(case.expected_m3),
                "capacity_case": CAPACITY_CASE,
                "allocation_order": row["allocation_order"],
                "origin_province": row["province"],
                "province_offer_m3": row["province_offer_m3"],
                "facility_id": row["facility_id"],
                "facility_name": row["facility_name"],
                "facility_class": row["facility_class"],
                "facility_province": row["facility_province"],
                "cross_border": row["cross_border"],
                "allocation_phase": row["allocation_phase"],
                "capacity_status": row["capacity_status"],
                "capacity_evidence": row["capacity_evidence"],
                "capacity_m3_y": row["capacity_m3_y"],
                "floor_scale_factor": row["floor_scale_factor"],
                "proxy_low_m3_y": row["proxy_low_m3_y"],
                "proxy_high_m3_y": row["proxy_high_m3_y"],
                "assigned_m3": row["assigned_m3"],
                "remaining_capacity_m3": row["remaining_capacity_m3"],
                "utilization": row["utilization"],
                "distance_km": row["distance_km"],
                "road_distance_km": row["road_distance_km"],
                "cross_border_penalty_km": row["cross_border_penalty_km"],
                "rank_distance_km": row["rank_distance_km"],
                "source_id": row["source_id"],
                "source_url": row["source_url"],
                "capacity_source_id": row["capacity_source_id"],
                "capacity_source_url": row["capacity_source_url"],
                "evidence_tier": row["evidence_tier"],
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _product_residue_rows(
    case: HarvestCase,
    chains: Mapping[str, ScenarioChain],
    allocations: Sequence[FeedstockAllocation],
    lhv_gj_per_odt: float,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str], list[FeedstockAllocation]
    ] = defaultdict(list)
    for allocation in allocations:
        grouped[
            (allocation.origin_province, allocation.facility_class)
        ].append(allocation)

    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        chain = chains[province]
        for facility_class in CLASS_ORDER:
            group = grouped.get((province, facility_class), [])
            if not group:
                continue
            scopes = {item.allocation_scope for item in group}
            if len(scopes) != 1:
                raise ScenarioChainError(
                    f"{province}/{facility_class}: mixed allocation scopes"
                )
            feedstock = math.fsum(item.feedstock_m3 for item in group)
            product = math.fsum(
                item.product_quantity for item in group
            )
            residue_odt = math.fsum(
                item.process_residue_odt for item in group
            )
            residue_pj = residue_odt * lhv_gj_per_odt / PJ_GJ
            rows.append(
                {
                    "scenario": case.scenario,
                    "harvest_case_m3": _format(case.expected_m3),
                    "province": province,
                    "province_name": chain.province_name,
                    "record_type": "facility_process",
                    "facility_class": facility_class,
                    "feedstock_m3": _format(feedstock),
                    "product_quantity": _format(product),
                    "product_unit": group[0].product_unit,
                    "process_residue_odt": _format(residue_odt),
                    "process_residue_m3": _format(
                        residue_odt / RESIDUE_DENSITY_ODT_M3
                    ),
                    "process_residue_pj": _format(residue_pj),
                    "harvest_residue_gross_m3": _format(0.0),
                    "harvest_residue_gross_odt": _format(0.0),
                    "harvest_residue_gross_pj": _format(0.0),
                    "harvest_residue_recoverable_m3": _format(0.0),
                    "harvest_residue_recoverable_odt": _format(0.0),
                    "harvest_residue_recoverable_pj": _format(0.0),
                    "harvest_residue_unrecovered_m3": _format(0.0),
                    "harvest_residue_unrecovered_odt": _format(0.0),
                    "harvest_residue_unrecovered_pj": _format(0.0),
                    "facility_assigned": "true",
                    "allocation_scope": group[0].allocation_scope,
                    "conversion_basis": (
                        "product_chain_v1_effective_province_class_ratio_"
                        "scaled_by_harvest"
                    ),
                    "coefficient_tier": group[0].coefficient_tier,
                    "evidence_label": EVIDENCE_LABEL,
                    "observed": _bool_text(OBSERVED),
                    "policy_grade": _bool_text(POLICY_GRADE),
                }
            )
        rows.append(
            {
                "scenario": case.scenario,
                "harvest_case_m3": _format(case.expected_m3),
                "province": province,
                "province_name": chain.province_name,
                "record_type": "harvest_residue",
                "facility_class": "ALL",
                "feedstock_m3": _format(0.0),
                "product_quantity": _format(0.0),
                "product_unit": "",
                "process_residue_odt": _format(0.0),
                "process_residue_m3": _format(0.0),
                "process_residue_pj": _format(0.0),
                "harvest_residue_gross_m3": _format(
                    chain.harvest_residue_gross_m3
                ),
                "harvest_residue_gross_odt": _format(
                    chain.harvest_residue_gross_odt
                ),
                "harvest_residue_gross_pj": _format(
                    chain.harvest_residue_gross_odt
                    * lhv_gj_per_odt
                    / PJ_GJ
                ),
                "harvest_residue_recoverable_m3": _format(
                    chain.harvest_residue_recoverable_m3
                ),
                "harvest_residue_recoverable_odt": _format(
                    chain.harvest_residue_recoverable_odt
                ),
                "harvest_residue_recoverable_pj": _format(
                    chain.harvest_residue_recoverable_odt
                    * lhv_gj_per_odt
                    / PJ_GJ
                ),
                "harvest_residue_unrecovered_m3": _format(
                    chain.harvest_residue_unrecovered_m3
                ),
                "harvest_residue_unrecovered_odt": _format(
                    chain.harvest_residue_unrecovered_odt
                ),
                "harvest_residue_unrecovered_pj": _format(
                    chain.harvest_residue_unrecovered_odt
                    * lhv_gj_per_odt
                    / PJ_GJ
                ),
                "facility_assigned": "false",
                "allocation_scope": "province_only",
                "conversion_basis": (
                    "product_chain_v1_harvest_residue_scaled_by_harvest"
                ),
                "coefficient_tier": chain.coefficient_tier,
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _scenario_summary_row(
    case: HarvestCase,
    result: v2.ScenarioResult,
    chains: Mapping[str, ScenarioChain],
    facilities: Sequence[v3.Facility],
    assignments: Sequence[AssignmentLink],
    fallback_pools: int,
    lhv_gj_per_odt: float,
) -> dict[str, Any]:
    metrics = result.metrics
    offer_m3 = _finite_nonnegative(metrics["offer_m3"], "offer total")
    assigned_m3 = _finite_nonnegative(metrics["assigned_m3"], "assigned total")
    unassigned_raw = float(metrics["unassigned_m3"])
    if not math.isfinite(unassigned_raw):
        raise ScenarioChainError("unassigned total is not finite")
    unassigned_m3 = max(0.0, unassigned_raw)
    eligible_capacity = math.fsum(
        float(facility.capacity_m3_y or 0.0)
        for facility in eligible_facilities(facilities)
    )
    if eligible_capacity <= 0.0:
        raise ScenarioChainError(f"{case.scenario}: eligible capacity is zero")
    capacity_by_province_value = capacity_by_province(facilities)
    local_assigned: dict[str, float] = defaultdict(float)
    cross_by_province: dict[str, float] = defaultdict(float)
    for allocation in result.assignments:
        origin = allocation["province"]
        volume = _finite_nonnegative(
            allocation["assigned_m3"], f"{origin} assignment"
        )
        if allocation["allocation_phase"] == "local":
            local_assigned[origin] += volume
        elif allocation["allocation_phase"] == "cross_border":
            cross_by_province[origin] += volume
        else:
            raise ScenarioChainError(
                f"{origin}: unexpected allocation phase "
                f"{allocation['allocation_phase']!r}"
            )
    local_binding: list[str] = []
    local_binding_m3 = 0.0
    for province in PROVINCES:
        remaining = capacity_by_province_value[province] - local_assigned[province]
        if cross_by_province[province] > VOLUME_EPSILON_M3 and remaining <= 1e-6:
            local_binding.append(province)
            local_binding_m3 += cross_by_province[province]
    overflow_m3 = 0.0
    for assignment in assignments:
        overflow_m3 += max(
            0.0, assignment.assigned_m3 - assignment.capacity_m3_y
        )
    assigned_by_province: dict[str, float] = defaultdict(float)
    for assignment in assignments:
        assigned_by_province[assignment.origin_province] += assignment.assigned_m3
    for offer in result.province_summary:
        province = offer["province"]
        overflow_m3 += max(
            0.0,
            assigned_by_province[province] - float(offer["offer_m3"]),
        )
    if overflow_m3 < 1e-6:
        overflow_m3 = 0.0

    class_feedstock = {
        facility_class: math.fsum(
            chains[province].class_feedstock_m3[facility_class]
            for province in PROVINCES
        )
        for facility_class in CLASS_ORDER
    }
    class_product = {
        facility_class: math.fsum(
            chains[province].class_product[facility_class]
            for province in PROVINCES
        )
        for facility_class in CLASS_ORDER
    }
    process_residue_odt = math.fsum(
        chains[province].class_residue_odt[facility_class]
        for province in PROVINCES
        for facility_class in CLASS_ORDER
    )
    harvest_gross_odt = math.fsum(
        chains[province].harvest_residue_gross_odt
        for province in PROVINCES
    )
    harvest_recoverable_odt = math.fsum(
        chains[province].harvest_residue_recoverable_odt
        for province in PROVINCES
    )
    harvest_unrecovered_odt = math.fsum(
        chains[province].harvest_residue_unrecovered_odt
        for province in PROVINCES
    )
    process_residue_pj = process_residue_odt * lhv_gj_per_odt / PJ_GJ
    harvest_gross_pj = harvest_gross_odt * lhv_gj_per_odt / PJ_GJ
    harvest_recoverable_pj = (
        harvest_recoverable_odt * lhv_gj_per_odt / PJ_GJ
    )
    harvest_unrecovered_pj = (
        harvest_unrecovered_odt * lhv_gj_per_odt / PJ_GJ
    )
    published_capacity = math.fsum(
        float(facility.capacity_m3_y or 0.0)
        for facility in eligible_facilities(facilities)
        if facility.capacity is not None
        and facility.capacity.status == v3.PUBLISHED
    )
    proxy_capacity = eligible_capacity - published_capacity
    row = {
        "scenario": case.scenario,
        "harvest_case_m3": case.expected_m3,
        "harvest_m3": offer_m3,
        "capacity_case": CAPACITY_CASE,
        "assigned_m3": assigned_m3,
        "assigned_share": min(1.0, float(metrics["assigned_share"])),
        "unassigned_m3": unassigned_m3,
        "overflow_m3": overflow_m3,
        "overflow_flag": overflow_m3 > 1e-6,
        "eligible_capacity_m3_y": eligible_capacity,
        "published_capacity_m3_y": published_capacity,
        "proxy_capacity_m3_y": proxy_capacity,
        "capacity_utilization": assigned_m3 / eligible_capacity,
        "global_capacity_binding": (
            unassigned_m3 > VOLUME_EPSILON_M3
            and assigned_m3 >= eligible_capacity - 1e-6
        ),
        "local_capacity_binding_provinces": ";".join(local_binding),
        "local_capacity_binding_m3": local_binding_m3,
        "published_m3": float(metrics["published_m3"]),
        "proxy_m3": float(metrics["proxy_m3"]),
        "proxy_share_of_assigned": float(metrics["proxy_share_of_assigned"]),
        "cross_border_m3": float(metrics["cross_border_m3"]),
        "cross_border_share": float(metrics["cross_border_share"]),
        "facilities_used": int(metrics["facilities_used"]),
        "max_facility_m3": float(metrics["max_facility_m3"]),
        "max_facility_share": float(metrics["max_facility_share"]),
        "top5_share": float(metrics["top5_share"]),
        "hhi": float(metrics["hhi"]),
        "process_residue_odt": process_residue_odt,
        "process_residue_m3": process_residue_odt / RESIDUE_DENSITY_ODT_M3,
        "process_residue_pj": process_residue_pj,
        "harvest_residue_gross_odt": harvest_gross_odt,
        "harvest_residue_gross_pj": harvest_gross_pj,
        "harvest_residue_recoverable_odt": harvest_recoverable_odt,
        "harvest_residue_recoverable_pj": harvest_recoverable_pj,
        "harvest_residue_unrecovered_odt": harvest_unrecovered_odt,
        "harvest_residue_unrecovered_pj": harvest_unrecovered_pj,
        "total_residue_gross_odt": process_residue_odt + harvest_gross_odt,
        "total_residue_gross_pj": process_residue_pj + harvest_gross_pj,
        "total_residue_recoverable_odt": (
            process_residue_odt + harvest_recoverable_odt
        ),
        "total_residue_recoverable_pj": (
            process_residue_pj + harvest_recoverable_pj
        ),
        "national_class_fallback_pools": fallback_pools,
        "lhv_gj_per_odt": lhv_gj_per_odt,
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }
    row["facility_feedstock_m3"] = math.fsum(class_feedstock.values())
    for facility_class, column in CLASS_FEEDSTOCK_COLUMN.items():
        row[column] = class_feedstock[facility_class]
    for facility_class, column in PRODUCT_SUMMARY_FIELDS.items():
        row[column] = class_product[facility_class]
    return row


def _comparison_rows(
    summaries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    baseline = summaries[PRIMARY_SCENARIO]
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIO_ORDER:
        summary = summaries[scenario]
        deltas = {
            metric: float(summary[metric]) - float(baseline[metric])
            for metric in (
                "harvest_m3",
                "assigned_m3",
                "capacity_utilization",
                "total_residue_gross_pj",
                "total_residue_recoverable_pj",
                "process_residue_pj",
                "sawlog_product_m3",
                "pulp_product_odt",
                "panel_product_m3",
                "pellet_product_odt",
                "bio_product_mwh",
            )
        }
        rows.append(
            {
                "scenario": scenario,
                "harvest_m3": _format(summary["harvest_m3"]),
                "assigned_m3": _format(summary["assigned_m3"]),
                "unassigned_m3": _format(summary["unassigned_m3"]),
                "capacity_utilization": _format(
                    summary["capacity_utilization"]
                ),
                "cross_border_m3": _format(summary["cross_border_m3"]),
                "cross_border_share": _format(
                    summary["cross_border_share"]
                ),
                "proxy_share_of_assigned": _format(
                    summary["proxy_share_of_assigned"]
                ),
                "facilities_used": summary["facilities_used"],
                "max_facility_share": _format(
                    summary["max_facility_share"]
                ),
                "hhi": _format(summary["hhi"]),
                "total_residue_gross_pj": _format(
                    summary["total_residue_gross_pj"]
                ),
                "total_residue_recoverable_pj": _format(
                    summary["total_residue_recoverable_pj"]
                ),
                "process_residue_pj": _format(summary["process_residue_pj"]),
                "harvest_residue_gross_pj": _format(
                    summary["harvest_residue_gross_pj"]
                ),
                "harvest_residue_recoverable_pj": _format(
                    summary["harvest_residue_recoverable_pj"]
                ),
                "sawlog_product_m3": _format(summary["sawlog_product_m3"]),
                "pulp_product_odt": _format(summary["pulp_product_odt"]),
                "panel_product_m3": _format(summary["panel_product_m3"]),
                "pellet_product_odt": _format(summary["pellet_product_odt"]),
                "bio_product_mwh": _format(summary["bio_product_mwh"]),
                "delta_harvest_m3_vs_119m": _format(deltas["harvest_m3"]),
                "delta_assigned_m3_vs_119m": _format(deltas["assigned_m3"]),
                "delta_capacity_utilization_vs_119m": _format(
                    deltas["capacity_utilization"]
                ),
                "delta_total_residue_gross_pj_vs_119m": _format(
                    deltas["total_residue_gross_pj"]
                ),
                "delta_total_residue_recoverable_pj_vs_119m": _format(
                    deltas["total_residue_recoverable_pj"]
                ),
                "delta_process_residue_pj_vs_119m": _format(
                    deltas["process_residue_pj"]
                ),
                "delta_sawlog_product_m3_vs_119m": _format(
                    deltas["sawlog_product_m3"]
                ),
                "delta_pulp_product_odt_vs_119m": _format(
                    deltas["pulp_product_odt"]
                ),
                "delta_panel_product_m3_vs_119m": _format(
                    deltas["panel_product_m3"]
                ),
                "delta_pellet_product_odt_vs_119m": _format(
                    deltas["pellet_product_odt"]
                ),
                "delta_bio_product_mwh_vs_119m": _format(
                    deltas["bio_product_mwh"]
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _summary_csv_rows(
    summaries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    numeric_fields = {
        "harvest_case_m3",
        "harvest_m3",
        "assigned_m3",
        "assigned_share",
        "unassigned_m3",
        "overflow_m3",
        "eligible_capacity_m3_y",
        "published_capacity_m3_y",
        "proxy_capacity_m3_y",
        "capacity_utilization",
        "local_capacity_binding_m3",
        "published_m3",
        "proxy_m3",
        "proxy_share_of_assigned",
        "cross_border_m3",
        "cross_border_share",
        "max_facility_m3",
        "max_facility_share",
        "top5_share",
        "hhi",
        "facility_feedstock_m3",
        "sawlog_feedstock_m3",
        "pulp_feedstock_m3",
        "panel_feedstock_m3",
        "pellet_feedstock_m3",
        "bio_feedstock_m3",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_odt",
        "process_residue_m3",
        "process_residue_pj",
        "harvest_residue_gross_odt",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_odt",
        "harvest_residue_recoverable_pj",
        "harvest_residue_unrecovered_odt",
        "harvest_residue_unrecovered_pj",
        "total_residue_gross_odt",
        "total_residue_gross_pj",
        "total_residue_recoverable_odt",
        "total_residue_recoverable_pj",
        "lhv_gj_per_odt",
    }
    for scenario in SCENARIO_ORDER:
        summary = summaries[scenario]
        row: dict[str, Any] = {}
        for field in SCENARIO_SUMMARY_FIELDS:
            value = summary[field]
            if field in numeric_fields:
                row[field] = _format(float(value))
            elif field in {"overflow_flag", "global_capacity_binding"}:
                row[field] = _bool_text(bool(value))
            else:
                row[field] = value
        rows.append(row)
    return rows


def _validation_checks(
    summaries: Mapping[str, Mapping[str, Any]],
    assignments: Sequence[dict[str, Any]],
    product_rows: Sequence[dict[str, Any]],
    chains_by_scenario: Mapping[str, Mapping[str, ScenarioChain]],
    intensity_note: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}
        )

    assignment_scenarios = {row["scenario"] for row in assignments}
    add(
        "scenario_coverage",
        assignment_scenarios == set(SCENARIO_ORDER),
        f"assignment scenarios={sorted(assignment_scenarios)}",
    )

    conservation_ok = True
    for scenario in SCENARIO_ORDER:
        summary = summaries[scenario]
        total = float(summary["assigned_m3"]) + float(summary["unassigned_m3"])
        if abs(total - float(summary["harvest_m3"])) > 1e-3:
            conservation_ok = False
    add(
        "assignment_conservation",
        conservation_ok,
        "assigned + unassigned equals harvest in every scenario",
    )

    capacity_ok = True
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assigned = float(row["assigned_m3"])
        if capacity <= 0.0 or assigned > capacity + 1e-6:
            capacity_ok = False
    add(
        "capacity_bounds",
        capacity_ok,
        "every assignment row has finite capacity and assigned <= capacity",
    )

    product_conservation_ok = True
    for scenario in SCENARIO_ORDER:
        process_rows = [
            row
            for row in product_rows
            if row["scenario"] == scenario
            and row["record_type"] == "facility_process"
        ]
        feedstock = math.fsum(
            float(row["feedstock_m3"]) for row in process_rows
        )
        expected = math.fsum(
            chain.class_feedstock_m3[facility_class]
            for chain in chains_by_scenario[scenario].values()
            for facility_class in CLASS_ORDER
        )
        if abs(feedstock - expected) > 1e-3:
            product_conservation_ok = False
        process_residue = math.fsum(
            float(row["process_residue_odt"]) for row in process_rows
        )
        expected_residue = math.fsum(
            chain.class_residue_odt[facility_class]
            for chain in chains_by_scenario[scenario].values()
            for facility_class in CLASS_ORDER
        )
        if abs(process_residue - expected_residue) > 1e-3:
            product_conservation_ok = False
    add(
        "product_residue_conservation",
        product_conservation_ok,
        "class feedstock and process residue conserve in every scenario",
    )

    ordering_ok = True
    for metric in (
        "harvest_m3",
        "assigned_m3",
        "capacity_utilization",
        "total_residue_gross_pj",
        "total_residue_recoverable_pj",
        "process_residue_pj",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
    ):
        values = [float(summaries[scenario][metric]) for scenario in SCENARIO_ORDER]
        if not (values[0] < values[1] < values[2]):
            ordering_ok = False
    add(
        "scenario_ordering",
        ordering_ok,
        "100m < 119m < 150m for assigned volume, residue, products, and utilization",
    )

    proxy_ok = True
    for scenario in SCENARIO_ORDER:
        assigned = float(summaries[scenario]["assigned_m3"])
        proxy = float(summaries[scenario]["proxy_m3"])
        share = float(summaries[scenario]["proxy_share_of_assigned"])
        if assigned <= 0.0 or not (0.0 <= share <= 1.0):
            proxy_ok = False
        if abs(proxy / assigned - share) > 1e-6:
            proxy_ok = False
    add(
        "proxy_share",
        proxy_ok,
        "proxy share equals proxy volume / assigned volume and is in [0,1]",
    )

    overflow_ok = True
    for scenario in SCENARIO_ORDER:
        summary = summaries[scenario]
        if bool(summary["overflow_flag"]) or float(summary["overflow_m3"]) > 1e-6:
            overflow_ok = False
        unassigned = float(summary["unassigned_m3"])
        if unassigned > 1e-6 and not bool(summary["global_capacity_binding"]):
            overflow_ok = False
    add(
        "no_overflow_unless_true",
        overflow_ok,
        "no assignment above capacity or offer; unassigned only with binding capacity",
    )

    labels_ok = all(
        row["evidence_label"] == EVIDENCE_LABEL
        and row["observed"] == "false"
        and row["policy_grade"] == "false"
        for row in assignments
    ) and all(
        row["evidence_label"] == EVIDENCE_LABEL
        and row["observed"] == "false"
        and row["policy_grade"] == "false"
        for row in product_rows
    )
    add(
        "labels",
        labels_ok,
        "all assignment and product/residue rows carry screening labels",
    )

    intensity = {
        item["intensity_m3_per_ha_yr"]: item["status"]
        for item in intensity_note["intensity_screens"]
    }
    intensity_ok = (
        intensity.get(1.0) == "infeasible"
        and intensity.get(1.2) == "infeasible"
        and "150m" in summaries
    )
    add(
        "intensity_150m_separate",
        intensity_ok,
        "150m documented ceiling retained; 1.0/1.2 intensity screens infeasible and separate",
    )
    return checks


def _report(
    summary: Mapping[str, Any],
    summaries: Mapping[str, Mapping[str, Any]],
) -> str:
    labels = summary["labels"]
    lines = [
        f"# {ARTIFACT_TITLE}",
        "",
        (
            "Multi-scenario 100/119/150 Mm3 facility assignment and "
            f"product/residue chain on {CAPACITY_LABEL}. All values "
            "are model estimates: `observed=false`, `policy_grade=false`. No "
            "legal/AAC claim is made."
        ),
        "",
        "## Scenarios",
        "",
    ]
    lines += v2._md_table(
        ("Scenario", "Harvest Mm3", "Source", "Capacity case"),
        [
            (
                scenario,
                f"{float(summaries[scenario]['harvest_m3']) / 1e6:,.3f}",
                f"provincial_allocation/{scenario}.csv",
                CAPACITY_CASE,
            )
            for scenario in SCENARIO_ORDER
        ],
    )
    lines += [
        "",
        "## Assignment by scenario",
        "",
    ]
    lines += v2._md_table(
        (
            "Scenario",
            "Assigned Mm3",
            "Unassigned m3",
            "Cross-border Mm3",
            "Proxy share",
            "Facilities",
            "Max facility share",
            "HHI",
            "Capacity utilization",
        ),
        [
            (
                scenario,
                f"{float(row['assigned_m3']) / 1e6:,.3f}",
                f"{float(row['unassigned_m3']):,.0f}",
                f"{float(row['cross_border_m3']) / 1e6:,.3f}",
                f"{float(row['proxy_share_of_assigned']) * 100:,.1f}%",
                int(row["facilities_used"]),
                f"{float(row['max_facility_share']) * 100:,.2f}%",
                f"{float(row['hhi']):.5f}",
                f"{float(row['capacity_utilization']) * 100:,.1f}%",
            )
            for scenario, row in (
                (scenario, summaries[scenario]) for scenario in SCENARIO_ORDER
            )
        ],
    )
    lines += [
        "",
        "## Products/residue by scenario",
        "",
    ]
    lines += v2._md_table(
        (
            "Scenario",
            "Sawlog product Mm3",
            "Pulp product Million odt",
            "Panel product Mm3",
            "Pellet product Million odt",
            "Bio product GWh",
            "Process residue PJ",
            "Harvest gross PJ",
            "Harvest recoverable PJ",
            "Total gross PJ",
            "Total recoverable PJ",
        ),
        [
            (
                scenario,
                f"{float(summaries[scenario]['sawlog_product_m3']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['pulp_product_odt']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['panel_product_m3']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['pellet_product_odt']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['bio_product_mwh']) / 1000:,.1f}",
                f"{float(summaries[scenario]['process_residue_pj']):,.2f}",
                f"{float(summaries[scenario]['harvest_residue_gross_pj']):,.2f}",
                f"{float(summaries[scenario]['harvest_residue_recoverable_pj']):,.2f}",
                f"{float(summaries[scenario]['total_residue_gross_pj']):,.2f}",
                f"{float(summaries[scenario]['total_residue_recoverable_pj']):,.2f}",
            )
            for scenario in SCENARIO_ORDER
        ],
    )
    lines += [
        "",
        "## Capacity/proxy/overflow",
        "",
    ]
    lines += v2._md_table(
        (
            "Scenario",
            "Eligible capacity Mm3/yr",
            "Published assigned Mm3",
            "Proxy assigned Mm3",
            "Capacity utilization",
            "Global capacity binding",
            "Local binding provinces",
            "Unassigned m3",
            "Overflow m3",
        ),
        [
            (
                scenario,
                f"{float(summaries[scenario]['eligible_capacity_m3_y']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['published_m3']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['proxy_m3']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['capacity_utilization']) * 100:,.1f}%",
                _bool_text(bool(summaries[scenario]["global_capacity_binding"])),
                str(summaries[scenario]["local_capacity_binding_provinces"]) or "none",
                f"{float(summaries[scenario]['unassigned_m3']):,.0f}",
                f"{float(summaries[scenario]['overflow_m3']):,.0f}",
            )
            for scenario in SCENARIO_ORDER
        ],
    )
    capacity_basis = summary["capacity_basis"]
    lines += [
        "",
        "### Capacity v2 basis",
        "",
    ]
    lines += v2._md_table(
        ("Measure", "Value"),
        [
            ("Published records", capacity_basis["published_records"]),
            ("Proxy records", capacity_basis["proxy_records"]),
            (
                "Not-published records",
                capacity_basis["not_published_records"],
            ),
            (
                "Published capacity Mm3/yr",
                f"{float(capacity_basis['published_capacity_m3_y']) / 1e6:,.3f}",
            ),
            (
                "Proxy capacity Mm3/yr",
                f"{float(capacity_basis['proxy_capacity_m3_y']) / 1e6:,.3f}",
            ),
            (
                "Total capacity Mm3/yr",
                f"{float(capacity_basis['total_capacity_m3_y']) / 1e6:,.3f}",
            ),
            (
                "Eligible assignment capacity Mm3/yr",
                f"{float(capacity_basis['eligible_capacity_m3_y']) / 1e6:,.3f}",
            ),
            (
                "Unlocated capacity excluded Mm3/yr",
                f"{float(capacity_basis['unlocated_capacity_m3_y']) / 1e6:,.3f}",
            ),
            (
                "Non-operating records excluded",
                capacity_basis["non_operating_records"],
            ),
        ],
    )
    lines += [
        "",
        "## Scenario comparison",
        "",
    ]
    lines += v2._md_table(
        (
            "Scenario",
            "Harvest Mm3",
            "Assigned Mm3",
            "Utilization",
            "Total gross PJ",
            "Total recoverable PJ",
            "Delta assigned vs 119m",
            "Delta gross PJ vs 119m",
        ),
        [
            (
                scenario,
                f"{float(summaries[scenario]['harvest_m3']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['assigned_m3']) / 1e6:,.3f}",
                f"{float(summaries[scenario]['capacity_utilization']) * 100:,.1f}%",
                f"{float(summaries[scenario]['total_residue_gross_pj']):,.2f}",
                f"{float(summaries[scenario]['total_residue_recoverable_pj']):,.2f}",
                (
                    f"{float(summaries[scenario]['assigned_m3']) - float(summaries['119m']['assigned_m3']):+,.0f}"
                ),
                (
                    f"{float(summaries[scenario]['total_residue_gross_pj']) - float(summaries['119m']['total_residue_gross_pj']):+,.2f}"
                ),
            )
            for scenario in SCENARIO_ORDER
        ],
    )
    intensity = summary["intensity_note"]
    lines += [
        "",
        "## 150 Mm3 intensity note",
        "",
        intensity["documented_ceiling_note"],
        "",
        intensity["conflation_warning"],
        "",
    ]
    lines += v2._md_table(
        ("Intensity m3/ha/yr", "Status", "Deficit m3", "Capacity Mm3"),
        [
            (
                f"{item['intensity_m3_per_ha_yr']:.1f}",
                item["status"],
                f"{item['deficit_m3']:,.0f}" if item["deficit_m3"] else "—",
                f"{item['capacity_m3_y'] / 1e6:,.3f}",
            )
            for item in intensity["intensity_screens"]
        ],
    )
    lines += [
        "",
        "## Validation",
        "",
    ]
    lines += v2._md_table(
        ("Check", "Status", "Detail"),
        [
            (check["name"], check["status"], check["detail"])
            for check in summary["validation"]["checks"]
        ],
    )
    lines += [
        "",
        "## Caveats",
        "",
        *[f"- {caveat}" for caveat in summary["caveats"]],
        "",
        (
            f"Labels: `{labels['evidence_label']}`, "
            f"`observed={_bool_text(labels['observed']).lower()}`, "
            f"`policy_grade={_bool_text(labels['policy_grade']).lower()}`."
        ),
        "",
    ]
    return "\n".join(lines)


def _write_manifest(output_dir: Path, summary: Mapping[str, Any]) -> None:
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "scenarios": summary["scenarios"],
        "comparison": summary["comparison"],
        "intensity_note": summary["intensity_note"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    v2._write_json(output_dir / "manifest.json", manifest)


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build the multi-scenario facility chain and return its summary."""

    assert_no_unexpected_deletions()
    generated_utc = _utc_now()
    cases = harvest_cases()
    references, product_chain_summary = read_reference_chains()
    lhv_gj_per_odt, lhv_source = read_lhv()
    intensity_note = read_intensity_note()
    facilities = v4.read_facilities(CAPACITY_CSV, INVENTORY_CSV)
    if not eligible_facilities(facilities):
        raise ScenarioChainError(
            f"no eligible facilities under {CAPACITY_LABEL}"
        )

    results: dict[str, v2.ScenarioResult] = {}
    chains_by_scenario: dict[str, dict[str, ScenarioChain]] = {}
    assignments_by_scenario: dict[str, list[AssignmentLink]] = {}
    allocation_rows: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    offer_totals: dict[str, float] = {}
    centroid_methods: dict[str, str] = {}

    config = AllocationConfig(
        road_factor=v3.ROAD_FACTOR,
        cross_border_penalty_km=v3.CROSS_BORDER_PENALTY_KM,
    )
    for case in cases:
        harvest = read_harvest_case(case)
        offers, centroid_methods = read_offers(harvest)
        result = v3.allocate_scenario(
            offers,
            facilities,
            CAPACITY_CASE,
            config,
        )
        links = _assignment_links(result)
        chains = build_scenario_chains(references, harvest)
        allocations, fallback_pools = allocate_class_feedstock(chains, links)
        results[case.scenario] = result
        chains_by_scenario[case.scenario] = chains
        assignments_by_scenario[case.scenario] = links
        allocation_rows.extend(_scenario_assignment_rows(case, result))
        product_rows.extend(
            _product_residue_rows(
                case,
                chains,
                allocations,
                lhv_gj_per_odt,
            )
        )
        summaries[case.scenario] = _scenario_summary_row(
            case,
            result,
            chains,
            facilities,
            links,
            fallback_pools,
            lhv_gj_per_odt,
        )
        offer_totals[case.scenario] = math.fsum(harvest.values())

    comparison_rows = _comparison_rows(summaries)
    checks = _validation_checks(
        summaries,
        allocation_rows,
        product_rows,
        chains_by_scenario,
        intensity_note,
    )
    failed = [check for check in checks if check["status"] != "PASS"]
    if failed:
        raise ScenarioChainError(
            "scenario validation failed: "
            + " | ".join(check["name"] for check in failed)
        )

    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            CAPACITY_CSV,
            INVENTORY_CSV,
            BOUNDARY_GEOJSON,
            PRODUCT_CHAIN_CSV,
            PRODUCT_CHAIN_SUMMARY,
            RSYC_SUMMARY,
            *(case.path for case in cases),
            *(
                ALLOCATION_DIR
                / f"intensity_{intensity}"
                / "provincial_allocation_summary.json"
                for intensity in INTENSITY_CASES
            ),
        )
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "facility_assignment": FACILITY_ASSIGNMENT,
        },
        "scenario_order": list(SCENARIO_ORDER),
        "primary_scenario": PRIMARY_SCENARIO,
        "capacity_case": CAPACITY_CASE,
        "scenarios": summaries,
        "comparison": comparison_rows,
        "offer_totals_m3": offer_totals,
        "centroid_methods": centroid_methods,
        "capacity_basis": {
            **capacity_v2_basis(facilities),
            "proxy_evidence": v3.PROXY_EVIDENCE,
            "published_evidence": sorted(v4.PUBLISHED_EVIDENCE),
            "no_unbounded_sink": True,
        },
        "product_chain": {
            "artifact": product_chain_summary.get("artifact"),
            "reference_harvest_m3": 118_977_339.1,
            "conversion": (
                "product_chain_v1 effective province/class product and "
                "process-residue ratios, scaled by province harvest ratio"
            ),
            "harvest_residue_scope": "province_only",
            "process_residue_scope": "facility_assigned",
        },
        "lhv": {
            "value_gj_per_odt": lhv_gj_per_odt,
            "source": lhv_source,
        },
        "intensity_note": intensity_note,
        "validation": {
            "passed": not failed,
            "failed_check_count": len(failed),
            "checks": checks,
        },
        "caveats": [
            (
                "All values are model estimates; `observed=false` and "
                "`policy_grade=false`."
            ),
            (
                "The 100/119/150 Mm3 cases are planning allocations, not legal "
                "AACs, sustainable-supply determinations, or observed harvest "
                "outcomes. No legal/AAC claim is made."
            ),
            (
                "Capacity v2 mixes 2015 Mills DB/FFCM infeed proxies, converted "
                "2025 pellet-map finished-product volume, and BC class-median "
                "proxy capacity. Proxy capacity is not observed capacity, and "
                "the bases are not interchangeable."
            ),
            (
                "Converted pellet capacity is finished-product volume, not "
                "infeed-equivalent; no pellet yield, moisture, bark, grade, or "
                "recovery conversion is modelled."
            ),
            (
                "The allocation is local-first with a 500 km cross-border "
                "penalty. Cross-border volume is a screening result, not an "
                "interprovincial trade forecast."
            ),
            (
                "Product and residue quantities apply product-chain-v1 "
                "province/class coefficients to assigned class feedstock. They "
                "are not measured mill output."
            ),
            (
                "Process residue is facility-assigned and separate from "
                "provincial harvest residue; harvest residue is never assigned "
                "to a facility."
            ),
            (
                "The 150m documented ceiling is not the 1.0 or 1.2 m3/ha/yr "
                "operable-proxy intensity screen. Both intensity screens are "
                "infeasible at 150 Mm3 and are reported separately."
            ),
            (
                "No UI or public OSeCAN surface is changed by this artifact."
            ),
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
        "inputs": inputs,
        "method": {
            "assignment": (
                f"{ASSIGNMENT_LABEL} published_plus_proxy, local-first, "
                "finite capacity, 500 km cross-border penalty"
            ),
            "product_residue": (
                "product-chain-v1 effective province/class ratios applied to "
                "assigned class feedstock"
            ),
            "harvest_residue": "province-only; never facility-assigned",
            "labels": "model_estimate / observed=false / policy_grade=false",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    v2._write_csv(
        output_dir / "scenario_assignment.csv",
        SCENARIO_ASSIGNMENT_FIELDS,
        allocation_rows,
    )
    v2._write_csv(
        output_dir / "scenario_product_residue.csv",
        SCENARIO_PRODUCT_RESIDUE_FIELDS,
        product_rows,
    )
    v2._write_csv(
        output_dir / "scenario_summary.csv",
        SCENARIO_SUMMARY_FIELDS,
        _summary_csv_rows(summaries),
    )
    v2._write_csv(
        output_dir / "scenario_comparison.csv",
        SCENARIO_COMPARISON_FIELDS,
        comparison_rows,
    )
    (output_dir / REPORT_NAME).write_text(
        _report(summary, summaries), encoding="utf-8"
    )
    v2._write_json(output_dir / "summary.json", summary)
    _write_manifest(output_dir, summary)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "scenarios": summary["scenario_order"],
                "status": summary["status"],
                "validation": summary["validation"]["passed"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
