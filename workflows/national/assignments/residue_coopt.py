"""Co-optimised harvest- and sawmill-residue allocation over shared capacity.

Stage A assigns harvest residue to PEL facilities and Stage B assigns sawmill
residue to PAP/PEL/BIO recipients.  The two diagnostics use the same physical
recipient capacity but were solved independently.  This module parses both
streams once, keeps the Stage A log assignment fixed, and solves one
transportation LP over facility-level shared capacity:

    maximise  product revenue - transport - processing
    subject to source balance, shared recipient capacity, residue-type
               eligibility, and an existing NRN road/ferry link.

Harvest residue is the Stage A ``RESIDUE`` grade.  It may be consumed by the
classes that Stage A declares eligible for that grade (PEL and BIO); the
residue type is recorded from the receiving class (sawdust/shavings for PEL,
hogfuel for BIO).  PAP is not eligible for the generic Stage A residue grade.
Sawmill residue keeps its FFCM outfeed type and therefore its declared consumer
class.  ``other`` (internally used woodfuel) remains non-allocatable.

Every screen is fail-closed: a missing price, a missing transport link, or an
unknown eligibility pair excludes the pair and is reported; nothing is
synthesised.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pulp

from .residue import (
    CONSUMER_CLASS_BY_RESIDUE,
    DENSITY_ODT_M3,
)

__all__ = [
    "CooptError",
    "CooptLink",
    "CooptRecipient",
    "CooptResult",
    "CooptSource",
    "build_coopt_inputs",
    "run_coopt",
    "solve_coopt",
    "validate_coopt",
]

CODE_VERSION = "0.1.0"
DEFAULT_BC_DIR = Path("data/derived/national/bc_chain")
DEFAULT_PERIOD = "2028"
_TOLERANCE = 1e-6
_RELATIVE_TOLERANCE = 1e-7
_POSITIVE = 1e-9
HARVEST_TYPES_BY_CLASS = {"PEL": "sawdust_shavings", "BIO": "hogfuel"}
HARVEST_SOURCE_TYPE = "harvest_residue"
ALLOCATABLE_TYPES = ("chips", "sawdust_shavings", "hogfuel")


class CooptError(RuntimeError):
    """Raised when the co-optimisation cannot be built without inventing data."""


@dataclass(frozen=True, slots=True)
class CooptSource:
    source_id: str
    source_kind: str
    source_facility: str
    zone: str
    residue_type: str
    odt: float
    volume_m3: float
    moisture_wet_basis: float
    baseline_recipient: str
    evidence: str


@dataclass(frozen=True, slots=True)
class CooptRecipient:
    facility_id: str
    facility_class: str
    capacity_odt_y: float
    capacity_m3_y: float
    product: str
    product_revenue_cad_per_input_odt: float
    operating_cost_cad_per_input_odt: float
    price_status: str
    processing_lines: str
    coord_source: str

    def accepts(self, residue_type: str) -> bool:
        return CONSUMER_CLASS_BY_RESIDUE.get(residue_type) == self.facility_class


@dataclass(frozen=True, slots=True)
class CooptLink:
    source_id: str
    recipient_facility: str
    distance_km: float
    cost_cad_m3: float
    path_confidence: str
    evidence: str

    def transport_per_odt(self) -> float:
        return self.cost_cad_m3 / DENSITY_ODT_M3


@dataclass(frozen=True, slots=True)
class CooptAllocation:
    source: CooptSource
    recipient: CooptRecipient
    residue_type: str
    link: CooptLink
    odt: float

    @property
    def volume_m3(self) -> float:
        return self.odt / DENSITY_ODT_M3

    @property
    def transport_cost_cad(self) -> float:
        return self.volume_m3 * self.link.cost_cad_m3

    @property
    def processing_cost_cad(self) -> float:
        return self.odt * self.recipient.operating_cost_cad_per_input_odt

    @property
    def product_revenue_cad(self) -> float:
        return self.odt * self.recipient.product_revenue_cad_per_input_odt

    @property
    def net_contribution_cad(self) -> float:
        return (
            self.product_revenue_cad
            - self.transport_cost_cad
            - self.processing_cost_cad
        )


@dataclass(frozen=True, slots=True)
class CooptResult:
    period: str
    sources: tuple[CooptSource, ...]
    recipients: tuple[CooptRecipient, ...]
    links: tuple[CooptLink, ...]
    allocations: tuple[CooptAllocation, ...]
    solver_status: str
    objective_mode: str
    raw_solver_objective_cad: float
    excluded_pairs: tuple[dict[str, str], ...]

    @property
    def assigned_odt(self) -> float:
        return math.fsum(item.odt for item in self.allocations)

    @property
    def generated_odt(self) -> float:
        return math.fsum(source.odt for source in self.sources)

    @property
    def product_revenue_cad(self) -> float:
        return math.fsum(item.product_revenue_cad for item in self.allocations)

    @property
    def transport_cost_cad(self) -> float:
        return math.fsum(item.transport_cost_cad for item in self.allocations)

    @property
    def processing_cost_cad(self) -> float:
        return math.fsum(item.processing_cost_cad for item in self.allocations)

    @property
    def net_contribution_cad(self) -> float:
        return (
            self.product_revenue_cad
            - self.transport_cost_cad
            - self.processing_cost_cad
        )


@dataclass(frozen=True, slots=True)
class CooptCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class CooptValidation:
    checks: tuple[CooptCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise CooptError(f"missing co-optimisation input: {path}")
    return pd.read_csv(path)


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise CooptError(f"{name} is missing columns: {', '.join(missing)}")


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise CooptError(f"{name} is not finite: {value!r}")
    return number


def _build_harvest_sources(stage_a: pd.DataFrame, period: str) -> list[CooptSource]:
    _require_columns(
        stage_a,
        ("period", "zone", "facility_id", "grade", "volume_m3", "supply_id"),
        "bc_stage_a_assignment.csv",
    )
    residue = stage_a[
        (stage_a["period"].astype(str) == period)
        & (stage_a["grade"].astype(str) == "RESIDUE")
    ].copy()
    if residue.empty:
        raise CooptError(f"Stage A has no RESIDUE rows for {period}")
    grouped = (
        residue.groupby(["zone", "facility_id"], dropna=False)
        .agg(
            volume_m3=("volume_m3", "sum"),
            supply_rows=("supply_id", "nunique"),
        )
        .reset_index()
    )
    sources: list[CooptSource] = []
    for row in grouped.itertuples(index=False):
        volume = _finite(row.volume_m3, "Stage A residue volume_m3")
        if volume <= 0.0:
            continue
        sources.append(
            CooptSource(
                source_id=f"HARVEST::{row.zone}::{row.facility_id}",
                source_kind="harvest_residue",
                source_facility=str(row.zone),
                zone=str(row.zone),
                residue_type=HARVEST_SOURCE_TYPE,
                odt=volume * DENSITY_ODT_M3,
                volume_m3=volume,
                moisture_wet_basis=0.45,
                baseline_recipient=str(row.facility_id),
                evidence=(
                    "stage_a_2028_RESIDUE;"
                    f"zone={row.zone};baseline_facility={row.facility_id};"
                    f"supply_rows={int(row.supply_rows)}"
                ),
            )
        )
    if not sources:
        raise CooptError("no positive Stage A harvest-residue source survived")
    return sources


def _build_sawmill_sources(stage_b_supply: pd.DataFrame) -> list[CooptSource]:
    _require_columns(
        stage_b_supply,
        ("source_facility", "residue_type", "odt", "volume_m3", "allocatable"),
        "stage_b/residue_supply.csv",
    )
    allocatable = stage_b_supply[
        stage_b_supply["allocatable"].astype(str).str.lower().isin({"yes", "true", "1"})
    ]
    if allocatable.empty:
        raise CooptError("Stage B has no allocatable residue supply rows")
    grouped = (
        allocatable.groupby(["source_facility", "residue_type"], dropna=False)
        .agg(
            odt=("odt", "sum"),
            volume_m3=("volume_m3", "sum"),
            moisture=("moisture_wet_basis", "max"),
            evidence=("evidence", "first"),
        )
        .reset_index()
    )
    sources: list[CooptSource] = []
    for row in grouped.itertuples(index=False):
        residue_type = str(row.residue_type)
        if residue_type not in ALLOCATABLE_TYPES:
            continue
        odt = _finite(row.odt, "Stage B residue odt")
        if odt <= 0.0:
            continue
        sources.append(
            CooptSource(
                source_id=f"SAWMILL::{row.source_facility}::{residue_type}",
                source_kind="sawmill_residue",
                source_facility=str(row.source_facility),
                zone="",
                residue_type=residue_type,
                odt=odt,
                volume_m3=_finite(row.volume_m3, "Stage B residue volume_m3"),
                moisture_wet_basis=float(row.moisture),
                baseline_recipient="",
                evidence=str(row.evidence),
            )
        )
    if not sources:
        raise CooptError("no positive sawmill-residue source survived")
    return sources


def _build_recipients(stage_b_demand: pd.DataFrame) -> list[CooptRecipient]:
    _require_columns(
        stage_b_demand,
        (
            "recipient_facility",
            "facility_class",
            "processing_line",
            "capacity_odt_y",
            "capacity_m3_y",
            "product",
            "product_revenue_cad_per_input_odt",
            "operating_cost_cad_per_input_odt",
            "price_status",
            "coord_source",
        ),
        "stage_b/residue_demand.csv",
    )
    included = stage_b_demand[stage_b_demand["status"] == "included"].copy()
    if included.empty:
        raise CooptError("Stage B has no included residue recipients")
    included["product_revenue_cad_per_input_odt"] = pd.to_numeric(
        included["product_revenue_cad_per_input_odt"], errors="coerce"
    )
    included["operating_cost_cad_per_input_odt"] = pd.to_numeric(
        included["operating_cost_cad_per_input_odt"], errors="coerce"
    )
    if included["product_revenue_cad_per_input_odt"].isna().any():
        raise CooptError("an included recipient has no numeric product revenue")
    if (included["price_status"] != "available").any():
        raise CooptError("an included recipient has no available product price")
    rows: list[CooptRecipient] = []
    for facility_id, group in included.groupby("recipient_facility", sort=True):
        capacity = _finite(group["capacity_odt_y"].sum(), f"{facility_id} capacity")
        if capacity <= 0.0:
            raise CooptError(f"{facility_id} has non-positive capacity")
        weights = group["capacity_odt_y"].astype(float)
        revenue = float(
            (group["product_revenue_cad_per_input_odt"] * weights).sum() / capacity
        )
        operating = float(
            (group["operating_cost_cad_per_input_odt"] * weights).sum() / capacity
        )
        classes = set(group["facility_class"].astype(str))
        if len(classes) != 1:
            raise CooptError(f"{facility_id} has mixed facility classes: {classes}")
        rows.append(
            CooptRecipient(
                facility_id=str(facility_id),
                facility_class=classes.pop(),
                capacity_odt_y=capacity,
                capacity_m3_y=_finite(
                    group["capacity_m3_y"].sum(), f"{facility_id} capacity_m3"
                ),
                product=str(group["product"].iloc[0]),
                product_revenue_cad_per_input_odt=revenue,
                operating_cost_cad_per_input_odt=operating,
                price_status="available",
                processing_lines=";".join(
                    sorted(group["processing_line"].astype(str).unique())
                ),
                coord_source=str(group["coord_source"].iloc[0]),
            )
        )
    return rows


def _build_links(
    stage_a_links: pd.DataFrame,
    stage_b_links: pd.DataFrame,
    harvest_sources: Sequence[CooptSource],
    sawmill_sources: Sequence[CooptSource],
    recipients: Sequence[CooptRecipient],
) -> list[CooptLink]:
    _require_columns(
        stage_a_links,
        ("origin", "destination", "grade_id", "distance_km", "cost_cad_m3"),
        "bc_stage_a_transport_links.csv",
    )
    _require_columns(
        stage_b_links,
        ("source_facility", "recipient_facility", "distance_km", "cost_cad_m3"),
        "stage_b/stage_b_transport_links.csv",
    )
    recipient_ids = {recipient.facility_id for recipient in recipients}
    harvest_zones = {f"ZONE_{source.zone}" for source in harvest_sources}
    harvest = stage_a_links[
        (stage_a_links["grade_id"].astype(str) == "RESIDUE")
        & (stage_a_links["origin"].astype(str).isin(harvest_zones))
        & (stage_a_links["destination"].astype(str).isin(recipient_ids))
    ]
    links: list[CooptLink] = []
    harvest_by_zone: dict[str, list[CooptLink]] = defaultdict(list)
    for row in harvest.itertuples(index=False):
        link = CooptLink(
            source_id="",
            recipient_facility=str(row.destination),
            distance_km=_finite(row.distance_km, "Stage A link distance"),
            cost_cad_m3=_finite(row.cost_cad_m3, "Stage A link cost"),
            path_confidence=str(getattr(row, "path_confidence", "real_road_network")),
            evidence=str(getattr(row, "evidence", "stage_a_residue_link")),
        )
        harvest_by_zone[str(row.origin)].append(link)
    for source in harvest_sources:
        for link in harvest_by_zone.get(f"ZONE_{source.zone}", []):
            links.append(
                CooptLink(
                    source_id=source.source_id,
                    recipient_facility=link.recipient_facility,
                    distance_km=link.distance_km,
                    cost_cad_m3=link.cost_cad_m3,
                    path_confidence=link.path_confidence,
                    evidence=link.evidence,
                )
            )
    sawmill_ids = {source.source_facility for source in sawmill_sources}
    sawmill = stage_b_links[
        (stage_b_links["source_facility"].astype(str).isin(sawmill_ids))
        & (stage_b_links["recipient_facility"].astype(str).isin(recipient_ids))
    ]
    for row in sawmill.itertuples(index=False):
        links.append(
            CooptLink(
                source_id=f"SAWMILL::{row.source_facility}::*",
                recipient_facility=str(row.recipient_facility),
                distance_km=_finite(row.distance_km, "Stage B link distance"),
                cost_cad_m3=_finite(row.cost_cad_m3, "Stage B link cost"),
                path_confidence=str(row.path_confidence),
                evidence=str(row.evidence),
            )
        )
    if not links:
        raise CooptError("no co-optimisation transport link survived")
    return links


def build_coopt_inputs(
    repo_root: Path | str = Path("."),
    period: str = DEFAULT_PERIOD,
    bc_dir: Path | None = None,
) -> tuple[
    list[CooptSource], list[CooptRecipient], list[CooptLink], list[dict[str, str]]
]:
    """Parse both residue streams, shared recipients, and transport links."""
    root = Path(repo_root)
    bc = Path(bc_dir) if bc_dir else root / DEFAULT_BC_DIR
    stage_a = _read_csv(bc / "bc_stage_a_assignment.csv")
    stage_b_supply = _read_csv(bc / "stage_b" / "residue_supply.csv")
    stage_b_demand = _read_csv(bc / "stage_b" / "residue_demand.csv")
    stage_a_links = _read_csv(bc / "bc_stage_a_transport_links.csv")
    stage_b_links = _read_csv(bc / "stage_b" / "stage_b_transport_links.csv")

    harvest = _build_harvest_sources(stage_a, period)
    sawmill = _build_sawmill_sources(stage_b_supply)
    recipients = _build_recipients(stage_b_demand)
    links = _build_links(
        stage_a_links, stage_b_links, harvest, sawmill, recipients
    )
    sources = harvest + sawmill
    return sources, recipients, links, []


# ---------------------------------------------------------------------------
# Solve
# ---------------------------------------------------------------------------


def _clean(value: object) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value))


def _link_key(link: CooptLink, source: CooptSource) -> tuple[str, str]:
    return (source.source_id, link.recipient_facility)


def _feasible_pairs(
    sources: Sequence[CooptSource],
    recipients: Sequence[CooptRecipient],
    links: Sequence[CooptLink],
) -> tuple[
    dict[tuple[str, str, str], tuple[CooptSource, CooptRecipient, str, CooptLink]],
    tuple[dict[str, str], ...],
]:
    {recipient.facility_id: recipient for recipient in recipients}
    link_by_pair: dict[tuple[str, str], CooptLink] = {}
    for link in links:
        if link.source_id.endswith("::*"):
            # Sawmill links are type-neutral; key them by facility source later.
            continue
        link_by_pair[(link.source_id, link.recipient_facility)] = link
    sawmill_links: dict[tuple[str, str], CooptLink] = {}
    for link in links:
        if not link.source_id.endswith("::*"):
            continue
        facility = link.source_id.split("::")[1]
        sawmill_links[(facility, link.recipient_facility)] = link

    feasible: dict[
        tuple[str, str, str], tuple[CooptSource, CooptRecipient, str, CooptLink]
    ] = {}
    excluded: list[dict[str, str]] = []
    for source in sources:
        if source.odt <= 0.0:
            continue
        if source.source_kind == "harvest_residue":
            candidate_types = tuple(
                residue_type
                for residue_type in HARVEST_TYPES_BY_CLASS.values()
            )
        else:
            candidate_types = (source.residue_type,)
        for recipient in recipients:
            for residue_type in candidate_types:
                expected_class = CONSUMER_CLASS_BY_RESIDUE.get(residue_type)
                if expected_class is None or recipient.facility_class != expected_class:
                    continue
                if source.source_kind == "harvest_residue":
                    link = link_by_pair.get((source.source_id, recipient.facility_id))
                else:
                    link = link_by_pair.get(
                        (source.source_id, recipient.facility_id)
                    ) or sawmill_links.get(
                        (source.source_facility, recipient.facility_id)
                    )
                if link is None:
                    excluded.append(
                        {
                            "source_id": source.source_id,
                            "residue_type": residue_type,
                            "recipient_facility": recipient.facility_id,
                            "reason": "no_transport_link",
                        }
                    )
                    continue
                key = (source.source_id, residue_type, recipient.facility_id)
                feasible[key] = (source, recipient, residue_type, link)
    if not feasible:
        raise CooptError("no eligible source-recipient-residue pair has a link")
    return feasible, tuple(excluded)


def solve_coopt(
    sources: Sequence[CooptSource],
    recipients: Sequence[CooptRecipient],
    links: Sequence[CooptLink],
    *,
    period: str = DEFAULT_PERIOD,
    objective_mode: str = "service_max_then_profit",
) -> CooptResult:
    """Solve the shared-capacity residue LP and return allocations.

    ``profit_max`` maximises product revenue minus transport and processing.
    ``service_max_then_profit`` first maximises assigned residue, then maximises
    the same net-contribution expression subject to that floor.  The service
    first mode is the default because leaving an eligible residue stream
    unassigned while shared capacity exists is a diagnostic failure, not a
    commercial result.
    """
    if objective_mode not in {"profit_max", "service_max_then_profit"}:
        raise CooptError(f"unknown objective mode {objective_mode!r}")
    feasible, excluded = _feasible_pairs(sources, recipients, links)

    def build_problem() -> tuple[
        pulp.LpProblem, dict[tuple[str, str, str], pulp.LpVariable]
    ]:
        problem = pulp.LpProblem("bc_residue_coopt", pulp.LpMaximize)
        variables: dict[tuple[str, str, str], pulp.LpVariable] = {}
        for key in sorted(feasible):
            variables[key] = pulp.LpVariable(
                "x__" + _clean("__".join(key)), lowBound=0.0
            )
        by_source: dict[str, list[pulp.LpVariable]] = defaultdict(list)
        by_recipient: dict[str, list[pulp.LpVariable]] = defaultdict(list)
        for key, variable in variables.items():
            by_source[key[0]].append(variable)
            by_recipient[key[2]].append(variable)
        for source in sources:
            terms = by_source.get(source.source_id, [])
            if terms:
                problem += (
                    pulp.lpSum(terms) <= source.odt,
                    "supply__" + _clean(source.source_id),
                )
        for recipient in recipients:
            terms = by_recipient.get(recipient.facility_id, [])
            if terms:
                problem += (
                    pulp.lpSum(terms) <= recipient.capacity_odt_y,
                    "capacity__" + _clean(recipient.facility_id),
                )
        return problem, variables

    def net_expression(
        variables: Mapping[tuple[str, str, str], pulp.LpVariable],
    ) -> pulp.LpAffineExpression:
        return pulp.lpSum(
            variable
            * (
                feasible[key][1].product_revenue_cad_per_input_odt
                - feasible[key][1].operating_cost_cad_per_input_odt
                - feasible[key][3].transport_per_odt()
            )
            for key, variable in variables.items()
        )

    solver = pulp.PULP_CBC_CMD(msg=False)
    if objective_mode == "profit_max":
        problem, variables = build_problem()
        problem += net_expression(variables)
        try:
            problem.solve(solver)
        except pulp.PulpSolverError as error:  # pragma: no cover - solver guard
            raise CooptError(f"co-optimisation solver failed: {error}") from error
        status = pulp.LpStatus.get(problem.status, f"status_{problem.status}")
        if status != "Optimal":
            raise CooptError(f"co-optimisation LP did not solve: status={status}")
        raw_objective = float(pulp.value(problem.objective) or 0.0)
    else:
        first, first_variables = build_problem()
        first += pulp.lpSum(first_variables.values())
        try:
            first.solve(solver)
        except pulp.PulpSolverError as error:  # pragma: no cover - solver guard
            raise CooptError(
                f"co-optimisation service-max stage failed: {error}"
            ) from error
        first_status = pulp.LpStatus.get(first.status, f"status_{first.status}")
        if first_status != "Optimal":
            raise CooptError(
                "co-optimisation service-max stage did not solve: "
                f"status={first_status}"
            )
        assigned_floor = float(pulp.value(first.objective) or 0.0)
        problem, variables = build_problem()
        problem += (
            pulp.lpSum(variables.values()) >= assigned_floor - 1e-6,
            "assigned_total_floor",
        )
        problem += net_expression(variables)
        try:
            problem.solve(solver)
        except pulp.PulpSolverError as error:  # pragma: no cover - solver guard
            raise CooptError(f"co-optimisation solver failed: {error}") from error
        status = pulp.LpStatus.get(problem.status, f"status_{problem.status}")
        if status != "Optimal":
            raise CooptError(f"co-optimisation LP did not solve: status={status}")
        raw_objective = float(pulp.value(problem.objective) or 0.0)

    allocations: list[CooptAllocation] = []
    for key, variable in sorted(variables.items()):
        value = variable.varValue
        odt = 0.0 if value is None else float(value)
        if odt <= _POSITIVE:
            continue
        source, recipient, residue_type, link = feasible[key]
        allocations.append(
            CooptAllocation(
                source=source,
                recipient=recipient,
                residue_type=residue_type,
                link=link,
                odt=odt,
            )
        )
    return CooptResult(
        period=period,
        sources=tuple(sources),
        recipients=tuple(recipients),
        links=tuple(links),
        allocations=tuple(allocations),
        solver_status=status,
        objective_mode=objective_mode,
        raw_solver_objective_cad=raw_objective,
        excluded_pairs=excluded,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_coopt(result: CooptResult) -> CooptValidation:
    """Check conservation, shared capacity, eligibility, links, and objective."""
    checks: list[CooptCheck] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(CooptCheck(name=name, passed=passed, detail=detail))

    def exceeds(value: float, limit: float) -> bool:
        return value > limit + max(
            _TOLERANCE, abs(limit) * _RELATIVE_TOLERANCE
        )

    assigned_by_source: dict[str, float] = defaultdict(float)
    assigned_by_recipient: dict[str, float] = defaultdict(float)
    for item in result.allocations:
        assigned_by_source[item.source.source_id] += item.odt
        assigned_by_recipient[item.recipient.facility_id] += item.odt
    source_ok = True
    source_detail: list[str] = []
    for source in result.sources:
        assigned = assigned_by_source.get(source.source_id, 0.0)
        if exceeds(assigned, source.odt):
            source_ok = False
            source_detail.append(
                f"{source.source_id}: {assigned:.6f} > {source.odt:.6f}"
            )
    add(
        "source_conservation",
        source_ok,
        "all sources assigned <= generated"
        if source_ok
        else "; ".join(source_detail),
    )

    capacity_ok = True
    capacity_detail: list[str] = []
    for recipient in result.recipients:
        received = assigned_by_recipient.get(recipient.facility_id, 0.0)
        if exceeds(received, recipient.capacity_odt_y):
            capacity_ok = False
            capacity_detail.append(
                f"{recipient.facility_id}: {received:.6f} > "
                f"{recipient.capacity_odt_y:.6f}"
            )
    add(
        "shared_capacity",
        capacity_ok,
        "no recipient exceeds facility-level shared capacity"
        if capacity_ok
        else "; ".join(capacity_detail),
    )

    eligibility_ok = True
    eligibility_detail: list[str] = []
    for item in result.allocations:
        expected = CONSUMER_CLASS_BY_RESIDUE.get(item.residue_type)
        if expected != item.recipient.facility_class:
            eligibility_ok = False
            eligibility_detail.append(
                f"{item.residue_type} -> {item.recipient.facility_class}"
            )
        if (
            item.source.source_kind == "harvest_residue"
            and item.residue_type not in HARVEST_TYPES_BY_CLASS.values()
        ):
            eligibility_ok = False
            eligibility_detail.append(
                f"harvest residue typed {item.residue_type}"
            )
    add(
        "residue_type_eligibility",
        eligibility_ok,
        "every allocation matches the declared consumer class"
        if eligibility_ok
        else "; ".join(sorted(set(eligibility_detail))),
    )

    link_ok = all(
        math.isfinite(item.link.cost_cad_m3)
        and item.link.cost_cad_m3 >= 0.0
        and math.isfinite(item.link.distance_km)
        for item in result.allocations
    )
    add(
        "transport_link_existence",
        link_ok,
        "every allocation has a finite, non-negative transport link",
    )

    objective_delta = abs(
        result.net_contribution_cad - result.raw_solver_objective_cad
    )
    add(
        "objective_reconciliation",
        objective_delta <= max(1e-3, abs(result.raw_solver_objective_cad) * 1e-5),
        f"assignment components - raw CBC objective = {objective_delta:.6f} CAD",
    )

    finite_ok = all(
        math.isfinite(item.odt) and item.odt >= 0.0
        for item in result.allocations
    )
    add("finite_nonnegative_values", finite_ok, "all allocated ODT are finite")
    return CooptValidation(tuple(checks))


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _allocation_rows(result: CooptResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(
        result.allocations,
        key=lambda allocation: (
            allocation.source.source_kind,
            allocation.source.source_id,
            allocation.recipient.facility_id,
        ),
    ):
        rows.append(
            {
                "period": result.period,
                "source_kind": item.source.source_kind,
                "source_id": item.source.source_id,
                "source_facility": item.source.source_facility,
                "zone": item.source.zone,
                "baseline_recipient": item.source.baseline_recipient,
                "residue_type": item.residue_type,
                "recipient_facility": item.recipient.facility_id,
                "facility_class": item.recipient.facility_class,
                "odt": item.odt,
                "volume_m3": item.volume_m3,
                "moisture_wet_basis": item.source.moisture_wet_basis,
                "distance_km": item.link.distance_km,
                "transport_cost_cad_m3": item.link.cost_cad_m3,
                "transport_cost_cad": item.transport_cost_cad,
                "processing_cost_cad_per_input_odt": (
                    item.recipient.operating_cost_cad_per_input_odt
                ),
                "processing_cost_cad": item.processing_cost_cad,
                "product": item.recipient.product,
                "product_revenue_cad": item.product_revenue_cad,
                "net_contribution_cad": item.net_contribution_cad,
                "path_confidence": item.link.path_confidence,
                "evidence": item.source.evidence,
            }
        )
    return rows


def _balance_rows(result: CooptResult) -> list[dict[str, Any]]:
    assigned: dict[str, float] = defaultdict(float)
    for item in result.allocations:
        assigned[item.source.source_id] += item.odt
    rows: list[dict[str, Any]] = []
    for source in result.sources:
        got = assigned.get(source.source_id, 0.0)
        rows.append(
            {
                "source_id": source.source_id,
                "source_kind": source.source_kind,
                "source_facility": source.source_facility,
                "zone": source.zone,
                "residue_type": source.residue_type,
                "generated_odt": source.odt,
                "assigned_odt": got,
                "unassigned_odt": max(0.0, source.odt - got),
            }
        )
    return sorted(
        rows, key=lambda row: (row["source_kind"], row["source_id"], row["residue_type"])
    )


def _utilization_rows(result: CooptResult) -> list[dict[str, Any]]:
    received: dict[str, float] = defaultdict(float)
    harvest: dict[str, float] = defaultdict(float)
    sawmill: dict[str, float] = defaultdict(float)
    for item in result.allocations:
        received[item.recipient.facility_id] += item.odt
        if item.source.source_kind == "harvest_residue":
            harvest[item.recipient.facility_id] += item.odt
        else:
            sawmill[item.recipient.facility_id] += item.odt
    rows: list[dict[str, Any]] = []
    for recipient in sorted(result.recipients, key=lambda item: item.facility_id):
        got = received.get(recipient.facility_id, 0.0)
        rows.append(
            {
                "facility_id": recipient.facility_id,
                "facility_class": recipient.facility_class,
                "processing_lines": recipient.processing_lines,
                "product": recipient.product,
                "capacity_odt_y": recipient.capacity_odt_y,
                "capacity_m3_y": recipient.capacity_m3_y,
                "received_odt": got,
                "received_m3": got / DENSITY_ODT_M3,
                "harvest_residue_odt": harvest.get(recipient.facility_id, 0.0),
                "sawmill_residue_odt": sawmill.get(recipient.facility_id, 0.0),
                "unused_capacity_odt": recipient.capacity_odt_y - got,
                "utilization": got / recipient.capacity_odt_y,
                "capacity_binding": (
                    recipient.capacity_odt_y - got
                )
                <= max(_TOLERANCE, recipient.capacity_odt_y * _RELATIVE_TOLERANCE),
            }
        )
    return rows


def _separate_allocations(
    repo_root: Path, period: str
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Return separate Stage A+Stage B ODT by recipient and its split."""
    bc = repo_root / DEFAULT_BC_DIR
    stage_a = _read_csv(bc / "bc_stage_a_assignment.csv")
    residue = stage_a[
        (stage_a["period"].astype(str) == period)
        & (stage_a["grade"].astype(str) == "RESIDUE")
    ]
    stage_b = _read_csv(bc / "stage_b" / "stage_b_assignment.csv")
    split: dict[str, dict[str, float]] = defaultdict(
        lambda: {"stage_a": 0.0, "stage_b": 0.0}
    )
    for row in residue.itertuples(index=False):
        split[str(row.facility_id)]["stage_a"] += float(row.volume_m3) * DENSITY_ODT_M3
    for row in stage_b.itertuples(index=False):
        split[str(row.recipient_facility)]["stage_b"] += float(row.odt)
    total = {facility: values["stage_a"] + values["stage_b"] for facility, values in split.items()}
    return total, split


def _capacity_comparison(
    result: CooptResult, separate_total: Mapping[str, float], split: Mapping[str, Mapping[str, float]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for recipient in sorted(result.recipients, key=lambda item: item.facility_id):
        separate = separate_total.get(recipient.facility_id, 0.0)
        coopt = math.fsum(
            item.odt
            for item in result.allocations
            if item.recipient.facility_id == recipient.facility_id
        )
        rows.append(
            {
                "facility_id": recipient.facility_id,
                "facility_class": recipient.facility_class,
                "capacity_odt_y": recipient.capacity_odt_y,
                "stage_a_harvest_odt": split.get(recipient.facility_id, {}).get(
                    "stage_a", 0.0
                ),
                "stage_b_sawmill_odt": split.get(recipient.facility_id, {}).get(
                    "stage_b", 0.0
                ),
                "separate_total_odt": separate,
                "separate_double_booked_odt": max(0.0, separate - recipient.capacity_odt_y),
                "coopt_odt": coopt,
                "coopt_unused_odt": recipient.capacity_odt_y - coopt,
            }
        )
    return rows


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _manifest(
    out_dir: Path,
    repo_root: Path,
    inputs: Sequence[Path],
    outputs: Sequence[Path],
    caveats: Sequence[str],
) -> dict[str, Any]:
    return {
        "artifact_version": CODE_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "caveats": list(caveats),
        "inputs": {
            _relative(path, repo_root): _sha256(path) for path in sorted(inputs)
        },
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in sorted(outputs)
        },
    }


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(header) for header in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _type_balance_rows(
    balance_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"generated_odt": 0.0, "assigned_odt": 0.0, "unassigned_odt": 0.0}
    )
    for row in balance_rows:
        key = (str(row["source_kind"]), str(row["residue_type"]))
        for field in ("generated_odt", "assigned_odt", "unassigned_odt"):
            totals[key][field] += float(row[field])
    return [
        {
            "source_kind": key[0],
            "residue_type": key[1],
            **values,
        }
        for key, values in sorted(totals.items())
    ]


def _report(
    result: CooptResult,
    validation: CooptValidation,
    utilization: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    balance_rows: Sequence[Mapping[str, Any]],
    separate_total: Mapping[str, float],
) -> str:
    assigned_by_kind: dict[str, float] = defaultdict(float)
    generated_by_kind: dict[str, float] = defaultdict(float)
    for source in result.sources:
        generated_by_kind[source.source_kind] += source.odt
    for item in result.allocations:
        assigned_by_kind[item.source.source_kind] += item.odt
    bioenergy = math.fsum(
        item.odt for item in result.allocations if item.recipient.facility_class == "BIO"
    )
    double_booked = math.fsum(
        float(row["separate_double_booked_odt"]) for row in comparison
    )
    total_separate = math.fsum(separate_total.values())
    harvest_total = math.fsum(
        source.odt for source in result.sources if source.source_kind == "harvest_residue"
    )
    stage_b_total = max(0.0, total_separate - harvest_total)
    stage_b_bioenergy = math.fsum(
        float(row["stage_b_sawmill_odt"])
        for row in comparison
        if row["facility_class"] == "BIO"
    )
    pel_separate = math.fsum(
        float(row["separate_total_odt"])
        for row in comparison
        if row["facility_class"] == "PEL"
    )
    pel_capacity = math.fsum(
        float(row["capacity_odt_y"])
        for row in comparison
        if row["facility_class"] == "PEL"
    )
    pel_coopt = math.fsum(
        float(row["coopt_odt"])
        for row in comparison
        if row["facility_class"] == "PEL"
    )
    lines = [
        "# BC residue co-optimisation: harvest and sawmill streams over shared capacity",
        "",
        (f"Generated {datetime.now(UTC).date().isoformat()} for {result.period}. "
        "Diagnostic, not an operating plan."),
        "",
        "## Method",
        "",
        ("- Stage A log assignment is fixed.  Harvest residue is the Stage A "
        "`RESIDUE` grade, conserved by zone and baseline facility."),
        ("- Sawmill residue is the Stage B FFCM outfeed supply derived from the "
        "same Stage A sawlog assignment; `other` remains non-allocatable."),
        ("- One LP allocates both streams over facility-level PAP/PEL/BIO "
        "capacity, with residue-type eligibility and existing NRN road/ferry "
        "links.  It first maximises assigned residue, then maximises net "
        "contribution subject to that floor (`service_max_then_profit`)."),
        ("- Objective basis is identical to Stage B: product revenue minus road "
        "transport (CAD/m3 / 0.45 odt/m3) minus FFCM line operating cost."),
        ("- Capacity is facility-level, so the two streams cannot double-book a "
        "recipient; Stage B line capacities are summed where a facility has "
        "more than one line."),
        "",
        "## Sources and recipients",
        "",
        _md_table(
            ("stream", "generated odt", "assigned odt", "unassigned odt", "share"),
            [
                (
                    kind,
                    f"{generated_by_kind[kind]:,.0f}",
                    f"{assigned_by_kind[kind]:,.0f}",
                    f"{generated_by_kind[kind] - assigned_by_kind[kind]:,.0f}",
                    f"{assigned_by_kind[kind] / generated_by_kind[kind]:.2%}"
                    if generated_by_kind[kind]
                    else "n/a",
                )
                for kind in sorted(generated_by_kind)
            ],
        ),
        "",
        "### Assigned and unassigned by source kind and residue type (odt)",
        "",
        _md_table(
            ("stream", "residue type", "generated", "assigned", "unassigned", "assigned share"),
            [
                (
                    row["source_kind"],
                    row["residue_type"],
                    f"{float(row['generated_odt']):,.0f}",
                    f"{float(row['assigned_odt']):,.0f}",
                    f"{float(row['unassigned_odt']):,.0f}",
                    f"{float(row['assigned_odt']) / float(row['generated_odt']):.2%}"
                    if float(row["generated_odt"])
                    else "n/a",
                )
                for row in _type_balance_rows(balance_rows)
                if float(row["generated_odt"]) > 0.0
            ],
        ),
        "",
        _md_table(
            ("class", "capacity odt/y", "received odt", "utilization", "binding facilities"),
            [
                (
                    facility_class,
                    f"{sum(float(row['capacity_odt_y']) for row in utilization if row['facility_class'] == facility_class):,.0f}",
                    f"{sum(float(row['received_odt']) for row in utilization if row['facility_class'] == facility_class):,.0f}",
                    f"{sum(float(row['received_odt']) for row in utilization if row['facility_class'] == facility_class) / sum(float(row['capacity_odt_y']) for row in utilization if row['facility_class'] == facility_class):.2%}",
                    sum(
                        1
                        for row in utilization
                        if row["facility_class"] == facility_class and row["capacity_binding"]
                    ),
                )
                for facility_class in ("BIO", "PAP", "PEL")
            ],
        ),
        "",
        "## Results",
        "",
        (f"- Assigned: **{result.assigned_odt:,.0f} odt** of "
        f"{result.generated_odt:,.0f} odt generated "
        f"({result.assigned_odt / result.generated_odt:.2%})."),
        (f"- Bioenergy (BIO class): **{bioenergy:,.0f} odt**, "
        f"{bioenergy / result.assigned_odt:.2%} of assigned and "
        f"{bioenergy / result.generated_odt:.2%} of generated."),
        (f"- Product revenue: **CAD {result.product_revenue_cad:,.0f}**; "
        f"transport **CAD {result.transport_cost_cad:,.0f}**; "
        f"processing **CAD {result.processing_cost_cad:,.0f}**."),
        (f"- Net contribution objective: **CAD {result.net_contribution_cad:,.0f}** "
        f"({result.objective_mode}); raw CBC objective "
        f"CAD {result.raw_solver_objective_cad:,.2f}."),
        (f"- Separate Stage A+Stage B allocations would claim "
        f"**{total_separate:,.0f} odt** at shared recipients and exceed "
        f"facility capacity by **{double_booked:,.0f} odt**; the co-optimised "
        "solve books that capacity once."),
        "",
        "## Comparison to separate Stage B",
        "",
        (f"- Stage B alone assigned **{stage_b_total:,.0f} odt** and "
        f"**{stage_b_bioenergy:,.0f} odt** to BIO; the co-opt assigns "
        f"**{result.assigned_odt:,.0f} odt** and **{bioenergy:,.0f} odt** to BIO."),
        (f"- Stage B PEL receipts plus the Stage A PEL harvest assignment claim "
        f"**{pel_separate:,.0f} odt** against **{pel_capacity:,.0f} odt** of "
        f"shared PEL capacity; the co-opt books PEL once at "
        f"**{pel_coopt:,.0f} odt**."),
        (f"- No source/recipient infeasibility was found. "
        f"**{len(result.excluded_pairs)}** eligible pairs were excluded because "
        "no NRN road/ferry link exists (all BIO-4007, which lacks links from "
        "eight harvest sources and eight sawmill hogfuel sources)."),
        "",
        "## Recipient utilization",
        "",
        _md_table(
            (
                "facility",
                "class",
                "capacity odt/y",
                "received odt",
                "harvest odt",
                "sawmill odt",
                "utilization",
                "binding",
            ),
            [
                (
                    row["facility_id"],
                    row["facility_class"],
                    f"{float(row['capacity_odt_y']):,.0f}",
                    f"{float(row['received_odt']):,.0f}",
                    f"{float(row['harvest_residue_odt']):,.0f}",
                    f"{float(row['sawmill_residue_odt']):,.0f}",
                    f"{float(row['utilization']):.2%}",
                    "yes" if row["capacity_binding"] else "no",
                )
                for row in utilization
                if float(row["received_odt"]) > 0.0
            ],
        ),
        "",
        "## Capacity comparison",
        "",
        _md_table(
            (
                "facility",
                "class",
                "capacity",
                "Stage A harvest",
                "Stage B sawmill",
                "separate total",
                "double-booked",
                "co-opt",
            ),
            [
                (
                    row["facility_id"],
                    row["facility_class"],
                    f"{float(row['capacity_odt_y']):,.0f}",
                    f"{float(row['stage_a_harvest_odt']):,.0f}",
                    f"{float(row['stage_b_sawmill_odt']):,.0f}",
                    f"{float(row['separate_total_odt']):,.0f}",
                    f"{float(row['separate_double_booked_odt']):,.0f}",
                    f"{float(row['coopt_odt']):,.0f}",
                )
                for row in comparison
                if float(row["separate_double_booked_odt"]) > _TOLERANCE
                or float(row["coopt_odt"]) > _TOLERANCE
            ],
        ),
        "",
        "## Validation",
        "",
        _md_table(
            ("check", "passed", "detail"),
            [(check.name, "yes" if check.passed else "no", check.detail) for check in validation.checks],
        ),
        "",
        "## Gaps and next steps",
        "",
        ("- Contract maxima are apportionment-derived diagnostic entitlements, not "
        "supply agreements; the co-opt uses FFCM facility capacity."),
        ("- Stage A harvest residue sends zero ODT to BIO in the baseline.  The "
        "co-opt permits BIO because Stage A declares BIO eligible for `RESIDUE`; "
        "any positive BIO harvest allocation is a diagnostic reallocation."),
        ("- PAP is not eligible for the generic Stage A `RESIDUE` grade, so harvest "
        "residue cannot be booked as chips to PAP without a grade/eligibility "
        "change; this is the smallest eligibility fix if that option is wanted."),
        ("- Sawmill source coordinates are TL-hub proxies or recovered Mills DB "
        "points; source-level distances remain the main transport limitation."),
        ("- Route capacity, rail/water modes, and moisture blending limits are not "
        "modelled; all residue is 0.45 wet-basis."),
    ]
    return "\n".join(lines) + "\n"


def run_coopt(
    repo_root: Path | str = Path("."),
    period: str = DEFAULT_PERIOD,
    out_dir: Path | None = None,
) -> CooptResult:
    root = Path(repo_root)
    bc = root / DEFAULT_BC_DIR
    sources, recipients, links, _excluded = build_coopt_inputs(
        root, period=period, bc_dir=bc
    )
    result = solve_coopt(sources, recipients, links, period=period)
    validation = validate_coopt(result)
    if not validation.passed:
        failures = "; ".join(
            f"{check.name}: {check.detail}"
            for check in validation.checks
            if not check.passed
        )
        raise CooptError(f"co-optimisation validation failed: {failures}")

    destination = Path(out_dir) if out_dir else bc
    destination.mkdir(parents=True, exist_ok=True)
    allocation_rows = _allocation_rows(result)
    balance_rows = _balance_rows(result)
    utilization = _utilization_rows(result)
    separate_total, split = _separate_allocations(root, period)
    comparison = _capacity_comparison(result, separate_total, split)

    assignment_path = destination / "bc_residue_coopt_assignment.csv"
    utilization_path = destination / "bc_residue_coopt_utilization.csv"
    balance_path = destination / "bc_residue_coopt_balance.csv"
    summary_path = destination / "bc_residue_coopt_summary.json"
    report_path = destination / "BC_RESIDUE_COOPT_REPORT.md"
    _write_csv(
        assignment_path,
        allocation_rows,
        (
            "period",
            "source_kind",
            "source_id",
            "source_facility",
            "zone",
            "baseline_recipient",
            "residue_type",
            "recipient_facility",
            "facility_class",
            "odt",
            "volume_m3",
            "moisture_wet_basis",
            "distance_km",
            "transport_cost_cad_m3",
            "transport_cost_cad",
            "processing_cost_cad_per_input_odt",
            "processing_cost_cad",
            "product",
            "product_revenue_cad",
            "net_contribution_cad",
            "path_confidence",
            "evidence",
        ),
    )
    _write_csv(
        utilization_path,
        utilization,
        (
            "facility_id",
            "facility_class",
            "processing_lines",
            "product",
            "capacity_odt_y",
            "capacity_m3_y",
            "received_odt",
            "received_m3",
            "harvest_residue_odt",
            "sawmill_residue_odt",
            "unused_capacity_odt",
            "utilization",
            "capacity_binding",
        ),
    )
    _write_csv(
        balance_path,
        balance_rows,
        (
            "source_id",
            "source_kind",
            "source_facility",
            "zone",
            "residue_type",
            "generated_odt",
            "assigned_odt",
            "unassigned_odt",
        ),
    )
    bioenergy = math.fsum(
        item.odt for item in result.allocations if item.recipient.facility_class == "BIO"
    )
    double_booked = math.fsum(
        float(row["separate_double_booked_odt"]) for row in comparison
    )
    summary = {
        "artifact_version": CODE_VERSION,
        "period": period,
        "objective_mode": result.objective_mode,
        "solver_status": result.solver_status,
        "generated_odt": result.generated_odt,
        "assigned_odt": result.assigned_odt,
        "unassigned_odt": result.generated_odt - result.assigned_odt,
        "assigned_share": result.assigned_odt / result.generated_odt,
        "bioenergy_odt": bioenergy,
        "bioenergy_share_of_assigned": bioenergy / result.assigned_odt,
        "bioenergy_share_of_generated": bioenergy / result.generated_odt,
        "objective": [
            {
                "component": "product_revenue",
                "value_cad": result.product_revenue_cad,
                "sense": "positive",
            },
            {
                "component": "transport_cost",
                "value_cad": result.transport_cost_cad,
                "sense": "negative",
            },
            {
                "component": "processing_cost",
                "value_cad": result.processing_cost_cad,
                "sense": "negative",
            },
            {
                "component": "net_contribution",
                "value_cad": result.net_contribution_cad,
                "sense": "objective_profit_max",
            },
        ],
        "raw_solver_objective_cad": result.raw_solver_objective_cad,
        "objective_reconciliation_delta_cad": (
            result.net_contribution_cad - result.raw_solver_objective_cad
        ),
        "capacity_double_booking_avoided_odt": double_booked,
        "separate_total_at_recipients_odt": math.fsum(separate_total.values()),
        "residue_type_balance": _type_balance_rows(balance_rows),
        "harvest_assigned_by_destination_type": [
            {
                "residue_type": residue_type,
                "assigned_odt": math.fsum(
                    item.odt
                    for item in result.allocations
                    if item.source.source_kind == "harvest_residue"
                    and item.residue_type == residue_type
                ),
            }
            for residue_type in sorted(
                {
                    item.residue_type
                    for item in result.allocations
                    if item.source.source_kind == "harvest_residue"
                }
            )
        ],
        "excluded_pair_count": len(result.excluded_pairs),
        "excluded_pairs": list(result.excluded_pairs),
        "source_counts": {
            "harvest_source_count": sum(
                1 for source in result.sources if source.source_kind == "harvest_residue"
            ),
            "sawmill_source_count": sum(
                1 for source in result.sources if source.source_kind == "sawmill_residue"
            ),
            "recipient_count": len(result.recipients),
            "allocation_count": len(result.allocations),
        },
        "class_utilization": [
            {
                "facility_class": facility_class,
                "capacity_odt_y": sum(
                    float(row["capacity_odt_y"])
                    for row in utilization
                    if row["facility_class"] == facility_class
                ),
                "received_odt": sum(
                    float(row["received_odt"])
                    for row in utilization
                    if row["facility_class"] == facility_class
                ),
                "binding_facility_count": sum(
                    1
                    for row in utilization
                    if row["facility_class"] == facility_class
                    and row["capacity_binding"]
                ),
            }
            for facility_class in ("BIO", "PAP", "PEL")
        ],
        "validation": [
            {"check": check.name, "passed": check.passed, "detail": check.detail}
            for check in validation.checks
        ],
        "data_gaps": [
            "PAP cannot accept the generic Stage A RESIDUE grade; harvest chips to PAP require an eligibility change",
            "Stage A harvest residue baseline BIO assignment is zero; BIO receipts in this run are diagnostic",
            "route capacity, rail/water modes, and moisture blending are absent",
            "TL-hub proxy coordinates remain for some sawmill sources",
        ],
    }
    summary_path.write_text(
        json.dumps(_json_ready(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        _report(
            result, validation, utilization, comparison, balance_rows, separate_total
        ),
        encoding="utf-8",
    )

    manifest_path = destination / "manifest.json"
    caveats = [
        "Stage A log assignment is fixed; only harvest and sawmill residue are co-optimised",
        "harvest residue is eligible at PEL and BIO under the Stage A RESIDUE grade, not PAP",
        "recipient capacity is facility-level FFCM line infeed capacity, shared across streams",
        "road and NRN ferry shortest paths only; no route capacity, rail, or water option",
        "contracts are apportionment-derived diagnostic maxima, not supply agreements",
    ]
    input_paths = [
        bc / "bc_stage_a_assignment.csv",
        bc / "bc_stage_a_transport_links.csv",
        bc / "stage_b" / "residue_supply.csv",
        bc / "stage_b" / "residue_demand.csv",
        bc / "stage_b" / "stage_b_transport_links.csv",
        bc / "stage_b" / "stage_b_assignment.csv",
    ]
    output_paths = [
        assignment_path,
        utilization_path,
        balance_path,
        summary_path,
        report_path,
    ]
    new_manifest = _manifest(destination, root, input_paths, output_paths, caveats)
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        merged_outputs = dict(existing.get("outputs", {}))
        merged_outputs.update(new_manifest["outputs"])
        existing["outputs"] = merged_outputs
        existing_inputs = dict(existing.get("inputs", {}))
        existing_inputs.update(new_manifest["inputs"])
        existing["inputs"] = existing_inputs
        existing_caveats = list(existing.get("caveats", []))
        for caveat in caveats:
            if caveat not in existing_caveats:
                existing_caveats.append(caveat)
        existing["caveats"] = existing_caveats
        existing["coopt_artifact_version"] = CODE_VERSION
        existing["coopt_generated_utc"] = new_manifest["generated_utc"]
        new_manifest = existing
    manifest_path.write_text(
        json.dumps(_json_ready(new_manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Co-optimise BC harvest and sawmill residue over shared capacity"
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    result = run_coopt(args.repo_root, args.period, args.out_dir)
    print(
        json.dumps(
            {
                "assigned_odt": result.assigned_odt,
                "generated_odt": result.generated_odt,
                "net_contribution_cad": result.net_contribution_cad,
                "allocations": len(result.allocations),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
