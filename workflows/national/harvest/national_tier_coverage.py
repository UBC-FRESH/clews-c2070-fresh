"""National evidence-tier coverage and source-replacement queue.

This module audits the 13-jurisdiction ``national_economics_v1`` scaffold at
component granularity.  It does **not** recompute prices, costs or margins.  It
turns the existing provincial price, logging+haul, road and silviculture tiers
into an explicit coverage table, then ranks the donor/assumption gaps for
source replacement.

The audit is deliberately conservative:

* only ``reported_average`` counts as direct/reported provincial evidence;
* ``proxy_model`` remains a model, not a direct observation;
* ``donor_proxy`` remains a donor, even when the donor value is published;
* assumptions are named, never hidden;
* the national total stays ``model_estimate_not_policy``.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_tier_coverage

Fail-closed rule: any unexpected ``D`` in the parent or ``data`` repository
status halts the run immediately.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ARTIFACT_VERSION",
    "COMPONENTS",
    "PROVINCES",
    "TIER_SCORE",
    "build",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
ECONOMICS_DIR = HARVEST_BASIS_DIR / "national_economics_v1"
PROVINCIAL_ALLOCATION_DIR = HARVEST_BASIS_DIR / "provincial_allocation"

PRICE_CSV = ECONOMICS_DIR / "national_price_proxies.csv"
COST_CSV = ECONOMICS_DIR / "national_cost_proxies.csv"
SOURCES_CSV = ECONOMICS_DIR / "sources.csv"
ASSUMPTIONS_CSV = ECONOMICS_DIR / "assumptions_donor_register.csv"
PARENT_SUMMARY_JSON = ECONOMICS_DIR / "summary.json"
ALLOCATION_CSV = PROVINCIAL_ALLOCATION_DIR / "119m.csv"
OPEN_DATA_POLICY_MD = REPO_ROOT / "planning/open-data-policy.md"
EVIDENCE_TIERS_MD = REPO_ROOT / "planning/national-evidence-tiers.md"

DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_tier_coverage"

ARTIFACT_VERSION = "1.0.0"
ARTIFACT = "national_tier_coverage"

PROVINCES: tuple[str, ...] = (
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
PROVINCE_NAMES: dict[str, str] = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}
COMPONENTS: tuple[str, ...] = ("price", "logging", "haul", "road", "silviculture")
COMPONENT_ORDER = {component: index for index, component in enumerate(COMPONENTS, 1)}
QUEUE_COMPONENT_ORDER = {component: index for index, component in enumerate(COMPONENTS)}

TIER_REPORTED = "reported_average"
TIER_PROXY = "proxy_model"
TIER_DONOR = "donor_proxy"
TIER_ASSUMPTION = "assumption"
TIER_SCORE = {
    TIER_REPORTED: 4,
    TIER_PROXY: 3,
    TIER_DONOR: 2,
    TIER_ASSUMPTION: 1,
}
QUEUE_TIERS = frozenset({TIER_DONOR, TIER_ASSUMPTION})

REQUIRED_SOURCE_CATEGORY = {
    "price": "price series",
    "logging": "cost study",
    "haul": "cost study",
    "road": "road/silviculture rate",
    "silviculture": "road/silviculture rate",
}
REPLACEMENT_ACTION = {
    "price": "obtain a provincial delivered-log price series",
    "logging": "obtain a provincial logging cost study",
    "haul": "obtain a provincial truck-haul cost study",
    "road": "obtain a provincial road allowance in CAD/m3",
    "silviculture": "obtain a provincial silviculture allowance in CAD/m3",
}

EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
NET_VALUE_STATUS = "model_estimate"
COMBINED_NATIONAL_NET_VALUE = "false"
STRICT_OPEN = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"

COVERAGE_CSV = "national_tier_coverage.csv"
SUMMARY_JSON = "national_tier_summary.json"
QUEUE_CSV = "source_replacement_queue.csv"
REPORT_MD = "TIER_COVERAGE_REPORT.md"
MANIFEST_JSON = "manifest.json"

RESTRICTED_MARKERS = ("confidential", "restricted", "microdata", "private_data")

COVERAGE_COLUMNS = (
    "coverage_cell_id",
    "province",
    "province_name",
    "component",
    "component_order",
    "component_basis",
    "tier",
    "tier_score",
    "source_id",
    "source_evidence_tier",
    "donor_province",
    "supporting_source_ids",
    "assumption_ids",
    "harvest_volume_m3",
    "volume_share",
    "direct_evidence",
    "proxy_model",
    "donor_or_assumption",
    "replacement_queue_eligible",
    "evidence",
    "observed",
    "policy_grade",
    "net_value_status",
    "combined_national_net_value",
    "strict_open",
    "gap_status",
    "notes",
)
QUEUE_COLUMNS = (
    "queue_rank",
    "priority",
    "province",
    "province_name",
    "component",
    "tier",
    "required_source_category",
    "replacement_action",
    "harvest_volume_m3",
    "volume_share",
    "current_source_id",
    "donor_province",
    "assumption_ids",
    "coverage_cell_id",
    "queue_status",
)


class TierCoverageError(ValueError):
    """Raised when an input or result violates the documented contract."""


@dataclass(frozen=True)
class CoverageCell:
    province: str
    component: str
    component_basis: str
    tier: str
    source_id: str
    donor_province: str
    supporting_source_ids: tuple[str, ...]
    assumption_ids: tuple[str, ...]
    harvest_volume_m3: float
    volume_share: float
    notes: str

    @property
    def coverage_cell_id(self) -> str:
        return f"{self.province}:{self.component}"

    @property
    def tier_score(self) -> int:
        return TIER_SCORE[self.tier]

    @property
    def direct_evidence(self) -> bool:
        return self.tier == TIER_REPORTED

    @property
    def proxy_model(self) -> bool:
        return self.tier == TIER_PROXY

    @property
    def donor_or_assumption(self) -> bool:
        return self.tier in QUEUE_TIERS

    @property
    def replacement_queue_eligible(self) -> bool:
        return self.donor_or_assumption


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise TierCoverageError(f"missing input file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise TierCoverageError(f"empty input table: {path}")
    return rows


def _require_columns(
    rows: Sequence[Mapping[str, str]], required: Iterable[str], source: Path
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise TierCoverageError(f"{source} is missing columns: {sorted(missing)}")


def _number(row: Mapping[str, str], column: str, context: str) -> float:
    raw = row.get(column, "")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise TierCoverageError(
            f"non-numeric {column} in {context}: {raw!r}"
        ) from exc
    if not math.isfinite(value):
        raise TierCoverageError(f"non-finite {column} in {context}: {value}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def _fmt_bool(value: bool) -> str:
    return "true" if value else "false"


def _deletion_lines(status_text: str) -> list[str]:
    """Return git-status porcelain lines with a staged or unstaged deletion."""
    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions(repo_root: Path) -> None:
    """Halt immediately on any worktree or index deletion."""
    repositories = [repo_root]
    data_root = repo_root / "data"
    if (data_root / ".git").exists():
        repositories.append(data_root)
    for repository in repositories:
        result = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise TierCoverageError(
                f"git status failed in {repository}: {result.stderr.strip()}"
            )
        deletions = _deletion_lines(result.stdout)
        if deletions:
            raise TierCoverageError(
                f"unexpected deletion in {repository}: {deletions[0]!r}; "
                "halting fail-closed"
            )


def _git_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def load_allocation_volumes(path: Path = ALLOCATION_CSV) -> dict[str, float]:
    """Load the 119 Mt provincial allocation and validate all jurisdictions."""
    rows = _read_csv(path)
    _require_columns(rows, ("province", "harvest_m3"), path)
    volumes: dict[str, float] = {}
    for row in rows:
        province = row["province"].strip()
        if province in volumes:
            raise TierCoverageError(f"duplicate allocation province: {province}")
        volume = _number(row, "harvest_m3", f"{path}:{province}")
        if volume < 0.0:
            raise TierCoverageError(
                f"negative allocation volume for {province}: {volume}"
            )
        volumes[province] = volume
    missing = set(PROVINCES) - set(volumes)
    extra = set(volumes) - set(PROVINCES)
    if missing or extra:
        raise TierCoverageError(
            f"allocation province mismatch; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    if sum(volumes.values()) <= 0.0:
        raise TierCoverageError("allocation volumes sum to zero")
    return volumes


def _load_province_rows(
    path: Path, required: Iterable[str]
) -> dict[str, dict[str, str]]:
    rows = _read_csv(path)
    _require_columns(rows, required, path)
    by_province: dict[str, dict[str, str]] = {}
    for row in rows:
        province = row["province"].strip()
        if province in by_province:
            raise TierCoverageError(f"duplicate province in {path}: {province}")
        by_province[province] = row
    missing = set(PROVINCES) - set(by_province)
    extra = set(by_province) - set(PROVINCES)
    if missing or extra:
        raise TierCoverageError(
            f"province mismatch in {path}; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    return by_province


def load_sources(path: Path = SOURCES_CSV) -> dict[str, dict[str, str]]:
    rows = _read_csv(path)
    _require_columns(
        rows,
        ("source_key", "evidence_tier", "strict_open", "licence"),
        path,
    )
    sources: dict[str, dict[str, str]] = {}
    for row in rows:
        source_key = row["source_key"].strip()
        if not source_key:
            raise TierCoverageError(f"empty source_key in {path}")
        if source_key in sources:
            raise TierCoverageError(f"duplicate source_key in {path}: {source_key}")
        sources[source_key] = row
    return sources


def load_assumption_ids(path: Path = ASSUMPTIONS_CSV) -> set[str]:
    rows = _read_csv(path)
    _require_columns(rows, ("assumption_id",), path)
    ids = {row["assumption_id"].strip() for row in rows}
    if "" in ids:
        raise TierCoverageError(f"empty assumption_id in {path}")
    return ids


def load_parent_label(path: Path = PARENT_SUMMARY_JSON) -> str:
    summary = json.loads(path.read_text(encoding="utf-8"))
    coverage = summary.get("coverage", {})
    if coverage.get("province_count") != len(PROVINCES):
        raise TierCoverageError(
            f"parent artifact does not cover {len(PROVINCES)} jurisdictions"
        )
    if sorted(coverage.get("provinces", [])) != sorted(PROVINCES):
        raise TierCoverageError("parent artifact province list does not match")
    label = summary.get("national_diagnostic", {}).get("label")
    if label != NATIONAL_DIAGNOSTIC_LABEL:
        raise TierCoverageError(
            f"unexpected parent national label: {label!r}; "
            f"expected {NATIONAL_DIAGNOSTIC_LABEL!r}"
        )
    return label


def _validate_source_id(
    source_id: str, sources: Mapping[str, Mapping[str, str]], context: str
) -> None:
    if source_id not in sources:
        raise TierCoverageError(f"unresolved source_id {source_id!r} in {context}")


def _tier_from_row(row: Mapping[str, str], column: str, context: str) -> str:
    tier = row.get(column, "").strip()
    if tier not in TIER_SCORE:
        raise TierCoverageError(
            f"unsupported tier {tier!r} in {context}; expected one of "
            f"{sorted(TIER_SCORE)}"
        )
    return tier


def _price_cell(
    province: str,
    row: Mapping[str, str],
    volume: float,
    volume_share: float,
    sources: Mapping[str, Mapping[str, str]],
) -> CoverageCell:
    context = f"price:{province}"
    tier = _tier_from_row(row, "price_tier", context)
    donor = row.get("donor_province", "").strip()
    source_id = "bc_log_market_2025_12"
    _validate_source_id(source_id, sources, context)
    if tier == TIER_REPORTED and donor:
        raise TierCoverageError(f"reported price row has a donor in {context}")
    if tier == TIER_DONOR and not donor:
        raise TierCoverageError(f"donor price row has no donor in {context}")
    if province == "BC":
        assumptions = ("price_bc_blend",)
        supporting: tuple[str, ...] = ()
    else:
        assumptions = ("price_non_bc_donor",)
        supporting = ("national_economics_v1_assumptions",)
    if province == "QC":
        assumptions = assumptions + ("price_qc_gap",)
    return CoverageCell(
        province=province,
        component="price",
        component_basis="mill_delivered_price_panel",
        tier=tier,
        source_id=source_id,
        donor_province=donor,
        supporting_source_ids=supporting,
        assumption_ids=assumptions,
        harvest_volume_m3=volume,
        volume_share=volume_share,
        notes=row.get("notes", "").strip(),
    )


def _logging_haul_cell(
    province: str,
    component: str,
    row: Mapping[str, str],
    volume: float,
    volume_share: float,
    sources: Mapping[str, Mapping[str, str]],
) -> CoverageCell:
    context = f"{component}:{province}"
    tier = _tier_from_row(row, "logging_haul_tier", context)
    source_id = "bc_open_cost_model"
    _validate_source_id(source_id, sources, context)
    if province == "BC":
        donor = ""
        assumptions: tuple[str, ...] = ("cost_bc_distribution",)
        supporting: tuple[str, ...] = ()
    else:
        donor = "BC"
        assumptions = (
            "cost_bc_distribution",
            "cost_wage_index",
            "cost_diesel_index",
            "cost_terrain_factor",
            "cost_road_factor",
        )
        supporting = (
            "national_economics_v1_assumptions",
            "slope_screen_960m",
            "operable_proxy_v2_road_distance",
        )
    return CoverageCell(
        province=province,
        component=component,
        component_basis="logging_haul_combined_distribution",
        tier=tier,
        source_id=source_id,
        donor_province=donor,
        supporting_source_ids=supporting,
        assumption_ids=assumptions,
        harvest_volume_m3=volume,
        volume_share=volume_share,
        notes=row.get("notes", "").strip(),
    )


def _road_cell(
    province: str,
    row: Mapping[str, str],
    volume: float,
    volume_share: float,
    sources: Mapping[str, Mapping[str, str]],
) -> CoverageCell:
    context = f"road:{province}"
    tier = _tier_from_row(row, "road_tier", context)
    source_id = "qc_cost_survey_2022"
    _validate_source_id(source_id, sources, context)
    donor = "" if province == "QC" else "QC"
    if tier == TIER_REPORTED and donor:
        raise TierCoverageError(f"reported road row has a donor in {context}")
    if tier == TIER_DONOR and not donor:
        raise TierCoverageError(f"donor road row has no donor in {context}")
    return CoverageCell(
        province=province,
        component="road",
        component_basis="road_allowance_cad_m3",
        tier=tier,
        source_id=source_id,
        donor_province=donor,
        supporting_source_ids=("national_economics_v1_assumptions",),
        assumption_ids=("road_allowance_qc_donor",),
        harvest_volume_m3=volume,
        volume_share=volume_share,
        notes=row.get("notes", "").strip(),
    )


def _silviculture_cell(
    province: str,
    row: Mapping[str, str],
    volume: float,
    volume_share: float,
    sources: Mapping[str, Mapping[str, str]],
) -> CoverageCell:
    context = f"silviculture:{province}"
    tier = _tier_from_row(row, "silviculture_tier", context)
    source_id = "bc_appraisal_silviculture_2026_10"
    _validate_source_id(source_id, sources, context)
    donor = "" if province == "BC" else "BC"
    if tier == TIER_REPORTED and donor:
        raise TierCoverageError(
            f"reported silviculture row has a donor in {context}"
        )
    if tier == TIER_DONOR and not donor:
        raise TierCoverageError(f"donor silviculture row has no donor in {context}")
    return CoverageCell(
        province=province,
        component="silviculture",
        component_basis="silviculture_allowance_cad_m3",
        tier=tier,
        source_id=source_id,
        donor_province=donor,
        supporting_source_ids=(),
        assumption_ids=("silviculture_allowance_bc_donor",),
        harvest_volume_m3=volume,
        volume_share=volume_share,
        notes=row.get("notes", "").strip(),
    )


def build_cells(
    prices: Mapping[str, Mapping[str, str]],
    costs: Mapping[str, Mapping[str, str]],
    volumes: Mapping[str, float],
    sources: Mapping[str, Mapping[str, str]],
) -> list[CoverageCell]:
    """Build one coverage cell per province x component."""
    total_volume = sum(volumes[province] for province in PROVINCES)
    if total_volume <= 0.0:
        raise TierCoverageError("national allocation volume must be positive")
    cells: list[CoverageCell] = []
    for province in PROVINCES:
        volume = volumes[province]
        share = volume / total_volume
        price_row = prices[province]
        cost_row = costs[province]
        cells.append(_price_cell(province, price_row, volume, share, sources))
        cells.append(
            _logging_haul_cell(
                province, "logging", cost_row, volume, share, sources
            )
        )
        cells.append(
            _logging_haul_cell(province, "haul", cost_row, volume, share, sources)
        )
        cells.append(_road_cell(province, cost_row, volume, share, sources))
        cells.append(
            _silviculture_cell(province, cost_row, volume, share, sources)
        )
    return cells


def validate_cells(cells: Sequence[CoverageCell]) -> None:
    expected = {
        (province, component)
        for province in PROVINCES
        for component in COMPONENTS
    }
    actual = [(cell.province, cell.component) for cell in cells]
    if len(actual) != len(set(actual)):
        raise TierCoverageError("duplicate province x component coverage cell")
    missing = expected - set(actual)
    extra = set(actual) - expected
    if missing or extra:
        raise TierCoverageError(
            f"coverage cell mismatch; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    for cell in cells:
        if cell.tier not in TIER_SCORE:
            raise TierCoverageError(f"invalid tier on {cell.coverage_cell_id}")
        if not cell.source_id:
            raise TierCoverageError(f"missing source_id on {cell.coverage_cell_id}")
        if not math.isfinite(cell.harvest_volume_m3):
            raise TierCoverageError(
                f"non-finite volume on {cell.coverage_cell_id}"
            )


def _source_evidence_tier(
    cell: CoverageCell, sources: Mapping[str, Mapping[str, str]]
) -> str:
    return sources[cell.source_id]["evidence_tier"]


def _coverage_row(
    cell: CoverageCell, sources: Mapping[str, Mapping[str, str]]
) -> dict[str, str]:
    return {
        "coverage_cell_id": cell.coverage_cell_id,
        "province": cell.province,
        "province_name": PROVINCE_NAMES[cell.province],
        "component": cell.component,
        "component_order": str(COMPONENT_ORDER[cell.component]),
        "component_basis": cell.component_basis,
        "tier": cell.tier,
        "tier_score": str(cell.tier_score),
        "source_id": cell.source_id,
        "source_evidence_tier": _source_evidence_tier(cell, sources),
        "donor_province": cell.donor_province,
        "supporting_source_ids": ";".join(cell.supporting_source_ids),
        "assumption_ids": ";".join(cell.assumption_ids),
        "harvest_volume_m3": _fmt(cell.harvest_volume_m3),
        "volume_share": _fmt(cell.volume_share, 10),
        "direct_evidence": _fmt_bool(cell.direct_evidence),
        "proxy_model": _fmt_bool(cell.proxy_model),
        "donor_or_assumption": _fmt_bool(cell.donor_or_assumption),
        "replacement_queue_eligible": _fmt_bool(
            cell.replacement_queue_eligible
        ),
        "evidence": EVIDENCE,
        "observed": OBSERVED,
        "policy_grade": POLICY_GRADE,
        "net_value_status": NET_VALUE_STATUS,
        "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
        "strict_open": STRICT_OPEN,
        "gap_status": "covered_explicit_tier",
        "notes": cell.notes,
    }


def _priority(volume_share: float) -> str:
    if volume_share <= 0.0:
        return "P4"
    if volume_share >= 0.10:
        return "P1"
    if volume_share >= 0.02:
        return "P2"
    return "P3"


def _queue_row(cell: CoverageCell, rank: int) -> dict[str, str]:
    return {
        "queue_rank": str(rank),
        "priority": _priority(cell.volume_share),
        "province": cell.province,
        "province_name": PROVINCE_NAMES[cell.province],
        "component": cell.component,
        "tier": cell.tier,
        "required_source_category": REQUIRED_SOURCE_CATEGORY[cell.component],
        "replacement_action": REPLACEMENT_ACTION[cell.component],
        "harvest_volume_m3": _fmt(cell.harvest_volume_m3),
        "volume_share": _fmt(cell.volume_share, 10),
        "current_source_id": cell.source_id,
        "donor_province": cell.donor_province,
        "assumption_ids": ";".join(cell.assumption_ids),
        "coverage_cell_id": cell.coverage_cell_id,
        "queue_status": "open_required_source_not_invented",
    }


def build_queue(cells: Sequence[CoverageCell]) -> list[tuple[CoverageCell, int]]:
    queued = [cell for cell in cells if cell.replacement_queue_eligible]
    queued.sort(
        key=lambda cell: (
            -cell.harvest_volume_m3,
            QUEUE_COMPONENT_ORDER[cell.component],
            cell.province,
        )
    )
    return [(cell, rank) for rank, cell in enumerate(queued, 1)]


def _tier_counts(cells: Sequence[CoverageCell]) -> dict[str, int]:
    counts = Counter(cell.tier for cell in cells)
    return {tier: counts.get(tier, 0) for tier in TIER_SCORE}


def _component_tier_counts(
    cells: Sequence[CoverageCell],
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for component in COMPONENTS:
        counts = Counter(
            cell.tier for cell in cells if cell.component == component
        )
        result[component] = {
            tier: counts.get(tier, 0) for tier in TIER_SCORE
        }
    return result


def _volume_weighted_key(
    total_volume: float, cells: Sequence[CoverageCell]
) -> dict[str, Any]:
    denominator = total_volume * len(COMPONENTS)
    if denominator <= 0.0:
        raise TierCoverageError("volume-weighted denominator is not positive")
    by_tier: dict[str, float] = {}
    for tier in TIER_SCORE:
        weighted_volume = sum(
            cell.harvest_volume_m3 for cell in cells if cell.tier == tier
        )
        by_tier[tier] = weighted_volume / denominator
    reported_share = by_tier[TIER_REPORTED]
    score_units = sum(
        cell.harvest_volume_m3 * cell.tier_score for cell in cells
    )
    return {
        "national_volume_m3": round(total_volume, 4),
        "component_count": len(COMPONENTS),
        "cell_volume_denominator_m3": round(denominator, 4),
        "tier_share": {
            tier: round(value, 12) for tier, value in by_tier.items()
        },
        "volume_weighted_reported_share": round(reported_share, 12),
        "volume_weighted_direct_share": round(reported_share, 12),
        "volume_weighted_direct_or_reported_share": round(reported_share, 12),
        "volume_weighted_proxy_model_share": round(by_tier[TIER_PROXY], 12),
        "volume_weighted_donor_or_assumption_share": round(
            by_tier[TIER_DONOR] + by_tier[TIER_ASSUMPTION], 12
        ),
        "volume_weighted_tier_score": round(score_units / denominator, 12),
        "volume_weighted_evidence_ratio": round(
            score_units / denominator / TIER_SCORE[TIER_REPORTED], 12
        ),
        "weighting": (
            "allocation harvest volume from provincial_allocation/119m.csv; "
            "each province has five equally weighted components"
        ),
    }


def _donor_key(cells: Sequence[CoverageCell]) -> dict[str, Any]:
    by_component: dict[str, dict[str, int]] = {}
    for component in COMPONENTS:
        counts = Counter(
            cell.donor_province
            for cell in cells
            if cell.component == component and cell.donor_province
        )
        by_component[component] = dict(sorted(counts.items()))
    donors = sorted(
        {
            cell.donor_province
            for cell in cells
            if cell.donor_province
        }
    )
    by_donor: dict[str, Any] = {}
    for donor in donors:
        donor_cells = [cell for cell in cells if cell.donor_province == donor]
        component_counts = Counter(cell.component for cell in donor_cells)
        recipients = sorted({cell.province for cell in donor_cells})
        by_donor[donor] = {
            "province_name": PROVINCE_NAMES[donor],
            "cell_count": len(donor_cells),
            "components": {
                component: component_counts.get(component, 0)
                for component in COMPONENTS
            },
            "recipient_provinces": recipients,
            "recipient_count": len(recipients),
        }
    return {
        "donor_provinces": donors,
        "donor_province_count": len(donors),
        "by_donor_province": by_donor,
        "by_component": by_component,
    }


def _coverage_key(cells: Sequence[CoverageCell]) -> dict[str, Any]:
    expected = {
        (province, component)
        for province in PROVINCES
        for component in COMPONENTS
    }
    actual = {(cell.province, cell.component) for cell in cells}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    return {
        "provinces": list(PROVINCES),
        "province_count": len(PROVINCES),
        "components": list(COMPONENTS),
        "component_count": len(COMPONENTS),
        "expected_cells": len(expected),
        "covered_cells": len(actual),
        "no_gap_count": len(actual),
        "silent_gap_count": 0,
        "missing_cells": ["{}:{}".format(*pair) for pair in missing],
        "unexpected_cells": ["{}:{}".format(*pair) for pair in extra],
        "gap_status": "none" if not missing and not extra else "gap_detected",
    }


def _queue_key(
    cells: Sequence[CoverageCell], queue: Sequence[tuple[CoverageCell, int]]
) -> dict[str, Any]:
    priority_counts = Counter(
        _priority(cell.volume_share) for cell, _ in queue
    )
    category_counts = Counter(
        REQUIRED_SOURCE_CATEGORY[cell.component] for cell, _ in queue
    )
    component_counts = Counter(cell.component for cell, _ in queue)
    second_wave = [cell for cell in cells if cell.tier == TIER_PROXY]
    return {
        "row_count": len(queue),
        "ranked_by": "provincial harvest volume descending",
        "priority_counts": {
            priority: priority_counts.get(priority, 0)
            for priority in ("P1", "P2", "P3", "P4")
        },
        "required_source_category_counts": dict(sorted(category_counts.items())),
        "component_counts": {
            component: component_counts.get(component, 0)
            for component in COMPONENTS
        },
        "second_wave_proxy_model_cells": len(second_wave),
        "second_wave_components": [
            cell.coverage_cell_id for cell in second_wave
        ],
        "queue_status": "open_required_source_not_invented",
    }


def build_summary(cells: Sequence[CoverageCell]) -> dict[str, Any]:
    total_volume = sum(
        volume
        for volume in (
            cell.harvest_volume_m3
            for cell in cells
            if cell.component == "price"
        )
    )
    counts = _tier_counts(cells)
    cell_count = len(cells)
    shares = {
        tier: round(count / cell_count, 12) for tier, count in counts.items()
    }
    queue = build_queue(cells)
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "coverage_audit",
        "observation_type": EVIDENCE,
        "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
        "scope": {
            "parent_artifact": "national_economics_v1",
            "provinces": list(PROVINCES),
            "components": list(COMPONENTS),
            "component_definition": (
                "logging and haul are separate coverage cells from the single "
                "combined logging_haul distribution; no new cost split is "
                "invented"
            ),
        },
        "coverage": _coverage_key(cells),
        "tier_counts": counts,
        "tier_shares": shares,
        "component_tier_counts": _component_tier_counts(cells),
        "volume_weighted": _volume_weighted_key(total_volume, cells),
        "donor_provinces": _donor_key(cells),
        "replacement_queue": _queue_key(cells, queue),
        "labels": {
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "combined_national_net_value": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "strict_open": False,
        },
        "validation": {
            "all_provinces_present": len({cell.province for cell in cells})
            == len(PROVINCES),
            "all_components_present": len({cell.component for cell in cells})
            == len(COMPONENTS),
            "expected_cell_count": cell_count
            == len(PROVINCES) * len(COMPONENTS),
            "no_duplicate_cells": len(
                {(cell.province, cell.component) for cell in cells}
            )
            == cell_count,
            "no_silent_gaps": not _coverage_key(cells)["missing_cells"],
            "all_tiers_supported": all(
                cell.tier in TIER_SCORE for cell in cells
            ),
            "direct_evidence_only_reported": all(
                cell.direct_evidence == (cell.tier == TIER_REPORTED)
                for cell in cells
            ),
            "donor_rows_remain_labelled": all(
                bool(cell.donor_province)
                for cell in cells
                if cell.tier == TIER_DONOR
            ),
            "queue_only_donor_or_assumption": all(
                cell.tier in QUEUE_TIERS for cell, _ in queue
            ),
            "queue_is_ranked": all(
                queue[index][0].harvest_volume_m3
                >= queue[index + 1][0].harvest_volume_m3
                for index in range(len(queue) - 1)
            ),
            "national_label_preserved": NATIONAL_DIAGNOSTIC_LABEL
            == "model_estimate_not_policy",
            "no_margin_recalculation": True,
            "strict_open_claim": False,
        },
        "limitations": [
            ("Coverage is an audit of existing tiers; no price, cost or margin "
            "is re-estimated."),
            ("Only reported_average is counted as direct/reported provincial "
            "evidence; proxy_model is not upgraded."),
            ("Donor values stay donor values even when the donor source is a "
            "published report."),
            ("Logging and haul are separate coverage cells but share the single "
            "combined logging_haul distribution; this audit does not invent a "
            "cost split."),
            ("NU has zero allocation volume; its evidence cells remain explicit "
            "and its queue rows rank last at P4."),
            ("The national total remains model_estimate_not_policy; this is not "
            "an observed or policy-grade result."),
        ],
    }
    return summary


def _write_csv(path: Path, rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _report_markdown(
    summary: Mapping[str, Any], cells: Sequence[CoverageCell]
) -> str:
    counts = summary["tier_counts"]
    shares = summary["tier_shares"]
    weighted = summary["volume_weighted"]
    donor = summary["donor_provinces"]
    queue = summary["replacement_queue"]
    coverage = summary["coverage"]
    generated_utc = summary["generated_utc"]
    lines: list[str] = [
        "# National Evidence-Tier Coverage and Replacement Queue",
        "",
        (f"Status: **model estimate scaffold audit — not policy grade, not "
        f"observed.** Generated {generated_utc}."),
        "",
        "## 1. Scope",
        "",
        (f"All **{coverage['province_count']} provinces and territories** are "
        f"covered at **{coverage['component_count']} components** each "
        f"(price, logging, haul, road, silviculture): "
        f"**{coverage['covered_cells']} of {coverage['expected_cells']}** "
        "coverage cells. There are **0 silent gaps**."),
        "",
        ("This audit does not recalculate prices, costs or margins. Logging and "
        "haul are separate coverage cells because the parent scaffold exposes "
        "them as one combined distribution; no logging/haul split is invented."),
        "",
        "## 2. Tier counts and shares",
        "",
        "| Tier | Score | Cells | Cell share | Volume-weighted share |",
        "|---|---:|---:|---:|---:|",
    ]
    for tier in (TIER_REPORTED, TIER_PROXY, TIER_DONOR, TIER_ASSUMPTION):
        lines.append(
            f"| `{tier}` | {TIER_SCORE[tier]} | {counts[tier]} | "
            f"{shares[tier]:.4f} | {weighted['tier_share'][tier]:.4f} |"
        )
    lines.extend(
        [
            "",
            ("`reported_average` is the only tier counted as direct/reported "
            "provincial evidence. `proxy_model` remains a model, "
            "`donor_proxy` remains a donor, and assumptions remain named "
            "assumptions."),
            "",
            "### Component detail",
            "",
            ("| Component | reported_average | proxy_model | donor_proxy | "
            "assumption |"),
            "|---|---:|---:|---:|---:|",
        ]
    )
    component_counts = summary["component_tier_counts"]
    for component in COMPONENTS:
        row = component_counts[component]
        lines.append(
            f"| {component} | {row[TIER_REPORTED]} | {row[TIER_PROXY]} | "
            f"{row[TIER_DONOR]} | {row[TIER_ASSUMPTION]} |"
        )
    lines.extend(
        [
            "",
            "## 3. Volume-weighted coverage",
            "",
            (f"- National allocation volume: "
            f"**{weighted['national_volume_m3']:,.1f} m3/yr**."),
            (f"- Volume-weighted direct/reported share: "
            f"**{weighted['volume_weighted_reported_share']:.4f}** "
            f"({weighted['volume_weighted_reported_share'] * 100:.2f}%)."),
            (f"- Volume-weighted proxy-model share: "
            f"**{weighted['volume_weighted_proxy_model_share']:.4f}**."),
            (f"- Volume-weighted donor/assumption share: "
            f"**{weighted['volume_weighted_donor_or_assumption_share']:.4f}**."),
            (f"- Volume-weighted tier score (0–4): "
            f"**{weighted['volume_weighted_tier_score']:.4f}**."),
            (f"- Volume-weighted evidence ratio: "
            f"**{weighted['volume_weighted_evidence_ratio']:.4f}**."),
            "",
            ("The denominator is five equally weighted components per province. "
            "Direct/reported volume therefore counts only `reported_average` "
            "cells; it is not inflated by donor or proxy cells."),
            "",
            "## 4. Donor provenance",
            "",
            f"Donor provinces: **{', '.join(donor['donor_provinces'])}**.",
            "",
            "| Donor | Cells | Price | Logging | Haul | Road | Silviculture |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for donor_province in donor["donor_provinces"]:
        item = donor["by_donor_province"][donor_province]
        components = item["components"]
        lines.append(
            f"| {donor_province} | {item['cell_count']} | "
            f"{components['price']} | {components['logging']} | "
            f"{components['haul']} | {components['road']} | "
            f"{components['silviculture']} |"
        )
    lines.extend(
        [
            "",
            ("BC is the price, logging+haul and silviculture donor. QC is the "
            "road donor. A donor value is never relabelled as the recipient's "
            "own evidence."),
            "",
            "## 5. Source-replacement queue",
            "",
            (f"**{queue['row_count']} donor/assumption rows** are queued by "
            "provincial harvest volume. Priority thresholds: P1 ≥10% of "
            "national volume, P2 ≥2%, P3 >0%, P4 zero volume."),
            "",
            "| Priority | Rows |",
            "|---|---:|",
        ]
    )
    for priority in ("P1", "P2", "P3", "P4"):
        lines.append(
            f"| {priority} | {queue['priority_counts'][priority]} |"
        )
    lines.extend(
        [
            "",
            "| Required source category | Rows |",
            "|---|---:|",
        ]
    )
    for category, count in queue["required_source_category_counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend(
        [
            "",
            "First rows:",
            "",
            ("| Rank | Priority | Province | Component | Tier | Required source | "
            "Volume (m3) |"),
            "|---:|---|---|---|---|---|---:|",
        ]
    )
    queue_rows = [cell for cell, _ in build_queue(cells)]
    for rank, cell in enumerate(queue_rows[:15], 1):
        lines.append(
            f"| {rank} | {_priority(cell.volume_share)} | {cell.province} | "
            f"{cell.component} | `{cell.tier}` | "
            f"{REQUIRED_SOURCE_CATEGORY[cell.component]} | "
            f"{cell.harvest_volume_m3:,.0f} |"
        )
    lines.extend(
        [
            "",
            ("The full ranked queue is in `source_replacement_queue.csv`. "
            "`proxy_model` cells are tracked as second-wave upgrades in the "
            "summary but are not mixed into the donor/assumption queue."),
            "",
            "## 6. Fail-closed and labels",
            "",
            ("- The builder halts on any unexpected `D` in parent or `data` git "
            "status."),
            ("- All 13 jurisdictions and all five components are present; "
            "missing cells fail the build."),
            ("- `evidence=model_estimate`, `observed=false`, "
            "`policy_grade=false`, `net_value_status=model_estimate`, "
            "`combined_national_net_value=false`."),
            ("- The national total remains "
            f"`{NATIONAL_DIAGNOSTIC_LABEL}`."),
            "- No new margin arithmetic is performed.",
            "",
            "## 7. Reproduction",
            "",
            "```bash",
            (".venv/bin/python -m workflows.national.harvest."
            "national_tier_coverage"),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _input_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TierCoverageError(f"missing manifest input: {path}")
    return {
        "path": _display_path(path),
        "sha256": _sha256(path),
        "bytes": int(path.stat().st_size),
    }


def _output_record(path: Path, rows: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": _display_path(path),
        "sha256": _sha256(path),
        "bytes": int(path.stat().st_size),
    }
    if rows is not None:
        record["rows"] = rows
    return record


def build(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Any]:
    """Build the coverage audit, queue, summary, report and manifest."""
    assert_no_unexpected_deletions(REPO_ROOT)
    out_dir.mkdir(parents=True, exist_ok=True)

    for path in (
        PRICE_CSV,
        COST_CSV,
        SOURCES_CSV,
        ASSUMPTIONS_CSV,
        PARENT_SUMMARY_JSON,
        ALLOCATION_CSV,
        OPEN_DATA_POLICY_MD,
        EVIDENCE_TIERS_MD,
    ):
        if not path.is_file():
            raise TierCoverageError(f"missing required input: {path}")

    volumes = load_allocation_volumes()
    prices = _load_province_rows(
        PRICE_CSV, ("province", "price_tier", "donor_province", "notes")
    )
    costs = _load_province_rows(
        COST_CSV,
        (
            "province",
            "logging_haul_tier",
            "road_tier",
            "silviculture_tier",
            "notes",
        ),
    )
    sources = load_sources()
    assumption_ids = load_assumption_ids()
    load_parent_label()

    cells = build_cells(prices, costs, volumes, sources)
    validate_cells(cells)
    for cell in cells:
        missing = set(cell.assumption_ids) - assumption_ids
        if missing:
            raise TierCoverageError(
                f"unresolved assumption ids on {cell.coverage_cell_id}: "
                f"{sorted(missing)}"
            )

    summary = build_summary(cells)
    queue = build_queue(cells)

    coverage_rows = [_coverage_row(cell, sources) for cell in cells]
    queue_rows = [_queue_row(cell, rank) for cell, rank in queue]
    _write_csv(out_dir / COVERAGE_CSV, coverage_rows, COVERAGE_COLUMNS)
    _write_csv(out_dir / QUEUE_CSV, queue_rows, QUEUE_COLUMNS)
    _write_json(out_dir / SUMMARY_JSON, summary)
    (out_dir / REPORT_MD).write_text(
        _report_markdown(summary, cells), encoding="utf-8"
    )

    input_paths = (
        PRICE_CSV,
        COST_CSV,
        SOURCES_CSV,
        ASSUMPTIONS_CSV,
        PARENT_SUMMARY_JSON,
        ALLOCATION_CSV,
        OPEN_DATA_POLICY_MD,
        EVIDENCE_TIERS_MD,
    )
    output_paths = (
        (out_dir / COVERAGE_CSV, len(coverage_rows)),
        (out_dir / QUEUE_CSV, len(queue_rows)),
        (out_dir / SUMMARY_JSON, None),
        (out_dir / REPORT_MD, None),
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "observation_type": EVIDENCE,
        "status": "coverage_audit",
        "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
        "labels": summary["labels"],
        "code": {
            "module": "workflows/national/harvest/national_tier_coverage.py",
            "parent_commit": _git_commit(REPO_ROOT),
            "data_commit": _git_commit(REPO_ROOT / "data"),
        },
        "inputs": [_input_record(path) for path in input_paths],
        "outputs": [
            _output_record(path, rows) for path, rows in output_paths
        ],
        "coverage": summary["coverage"],
        "tier_counts": summary["tier_counts"],
        "volume_weighted": summary["volume_weighted"],
        "donor_provinces": summary["donor_provinces"]["donor_provinces"],
        "replacement_queue": summary["replacement_queue"],
        "validation": summary["validation"],
    }
    _write_json(out_dir / MANIFEST_JSON, manifest)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Output directory for the coverage artifact.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = build(args.out_dir)
    except TierCoverageError as exc:
        print(f"national_tier_coverage: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "coverage": summary["coverage"]["covered_cells"],
                "silent_gaps": summary["coverage"]["silent_gap_count"],
                "tier_counts": summary["tier_counts"],
                "queue_rows": summary["replacement_queue"]["row_count"],
                "national_label": summary["national_diagnostic_label"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
