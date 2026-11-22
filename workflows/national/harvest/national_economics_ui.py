"""Export national economics v2.1 into the read-only live UI bundle.

The export is evidence-first: the default view is provincial evidence coverage,
not a national margin headline.  It is derived only from committed CSVs and
``summary.json``.  It never invents a national total: the two national rows are
volume-weighted diagnostic values and stay ``model_estimate_not_policy``.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_economics_ui
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "NationalEconomicsUiError",
    "build_export",
    "main",
    "write_export",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2_1"
)
TIER_SOURCE = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_tier_coverage"
    / "national_tier_coverage.csv"
)
TIER_SUMMARY_JSON = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_tier_coverage"
    / "national_tier_summary.json"
)
QUEUE_SOURCE = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_tier_coverage"
    / "source_replacement_queue.csv"
)
STUMPAGE_SOURCE = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2"
    / "provincial_stumpage_references.csv"
)
ALLOCATION_SOURCE = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)
OUT_JSON = REPO_ROOT / "ui/public/data/economics/national.json"
OUT_MANIFEST = REPO_ROOT / "ui/public/data/economics/national.manifest.json"

SCHEMA_VERSION = "economics/national/2"
MANIFEST_SCHEMA_VERSION = "economics/national-manifest/2"
MODEL_ID = "national_economics_v2_1"

COMPONENTS = ("price", "logging", "haul", "road", "silviculture")
TIERS = ("reported_average", "proxy_model", "donor_proxy", "assumption")
SCENARIO_KEYS = ("donor_flat", "stumpage_scaled")

MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
COMPARISON_CSV = "scenario_comparison.csv"
TIER_CSV = "tier_coverage.csv"
SUMMARY_JSON = "summary.json"


class NationalEconomicsUiError(RuntimeError):
    """Fail-closed export error."""


def _fail(message: str) -> None:
    raise NationalEconomicsUiError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _deletion_lines(status_text: str) -> list[str]:
    """Return staged or unstaged git-status deletion lines."""
    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions(repo_root: Path) -> None:
    """Halt on any worktree or index deletion in the parent or data repo."""
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
            _fail(f"git status failed in {repository}: {result.stderr.strip()}")
        deletions = _deletion_lines(result.stdout)
        if deletions:
            _fail(
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


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        _fail(f"missing source CSV: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        _fail(f"empty source CSV: {path}")
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"missing source JSON: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _fail(f"invalid source JSON {path}: {error}")
    if not isinstance(value, dict):
        _fail(f"source JSON is not an object: {path}")
    return value


def _number(row: dict[str, str], field: str, label: str) -> float:
    raw = row.get(field, "").strip()
    if not raw:
        _fail(f"{label}: missing {field}")
    try:
        value = float(raw)
    except ValueError as error:
        _fail(f"{label}: invalid {field}={raw!r}: {error}")
    if not math.isfinite(value):
        _fail(f"{label}: non-finite {field}")
    return value


def _optional_number(
    row: dict[str, str], field: str, label: str
) -> float | None:
    raw = row.get(field, "").strip()
    if not raw:
        return None
    return _number(row, field, label)


def _text(row: dict[str, str], field: str, label: str) -> str:
    value = row.get(field, "").strip()
    if not value:
        _fail(f"{label}: missing {field}")
    return value


def _boolean(row: dict[str, str], field: str, label: str) -> bool:
    value = _text(row, field, label).lower()
    if value not in {"true", "false"}:
        _fail(f"{label}: invalid boolean {field}={value!r}")
    return value == "true"


def _round(value: float, digits: int) -> float:
    return round(value, digits)


def _scenario_rows(
    rows: list[dict[str, str]],
    *,
    row_type: str,
) -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["price_scenario"], row["province"]): row
        for row in rows
        if row.get("row_type") == row_type
    }


def _scenario_total_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        row["price_scenario"]: row
        for row in rows
        if row.get("row_type") == "scenario_total"
    }


def _tier_counts(
    rows: list[dict[str, str]],
    row_type: str,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        if row.get("row_type") != row_type:
            continue
        scenario = row.get("price_scenario", "").strip()
        tier = (row.get("price_tier") or row.get("cost_tier") or "").strip()
        if not scenario or not tier:
            _fail(f"tier count row is incomplete: {row}")
        counts[scenario][tier] = int(_number(row, "tier_count", "tier count"))
    return dict(counts)


def _tier_coverage_audit(path: Path) -> dict[str, Any]:
    rows = _read_csv(path)
    provinces = {row.get("province", "").strip() for row in rows}
    components = {row.get("component", "").strip() for row in rows}
    tiers = {row.get("tier", "").strip() for row in rows}
    if len(provinces) != 13:
        _fail(f"tier audit covers {len(provinces)} provinces, expected 13")
    if components != set(COMPONENTS):
        _fail(f"tier audit components mismatch: {sorted(components)}")
    if tiers - set(TIERS):
        _fail(f"tier audit has unknown tiers: {sorted(tiers - set(TIERS))}")
    if len(rows) != len(provinces) * len(COMPONENTS):
        _fail("tier audit does not cover every province x component cell")

    volume_by_tier: dict[str, float] = defaultdict(float)
    total_cell_volume = 0.0
    for row in rows:
        tier = _text(row, "tier", "tier audit")
        volume = _number(row, "harvest_volume_m3", "tier audit")
        if volume < 0:
            _fail("tier audit has a negative harvest volume")
        volume_by_tier[tier] += volume
        total_cell_volume += volume
    if total_cell_volume <= 0:
        _fail("tier audit has no positive harvest volume")

    cell_counts = Counter(_text(row, "tier", "tier audit") for row in rows)
    cell_count = len(rows)
    shares = {
        tier: _round(volume_by_tier.get(tier, 0.0) / total_cell_volume, 8)
        for tier in TIERS
    }
    if abs(sum(shares.values()) - 1.0) > 1e-6:
        _fail("tier audit volume shares do not sum to 1")
    cell_shares = {
        tier: _round(cell_counts.get(tier, 0) / cell_count, 8)
        for tier in TIERS
    }
    return {
        "basis": "volume_weighted_five_component_cells",
        "component_count": len(components),
        "province_count": len(provinces),
        "cell_count": cell_count,
        "volume_weighted_shares": shares,
        "volume_weighted_pct": {
            tier: _round(share * 100, 4) for tier, share in shares.items()
        },
        "cell_counts": {tier: cell_counts.get(tier, 0) for tier in TIERS},
        "cell_shares": cell_shares,
        "cell_pct": {
            tier: _round(share * 100, 4) for tier, share in cell_shares.items()
        },
        "total_harvest_volume_m3": _round(total_cell_volume / len(COMPONENTS), 1),
    }


def _source_map(path: Path) -> dict[str, dict[str, str]]:
    rows = _read_csv(path)
    source_map: dict[str, dict[str, str]] = {}
    for row in rows:
        source_key = _text(row, "source_key", "sources")
        if source_key in source_map:
            _fail(f"duplicate source key: {source_key}")
        source_map[source_key] = row
    return source_map


def _tier_cells(
    rows: list[dict[str, str]],
    sources: dict[str, dict[str, str]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Build one evidence record per province x component cell."""
    by_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        province = _text(row, "province", "tier cell")
        component = _text(row, "component", "tier cell")
        tier = _text(row, "tier", "tier cell")
        if component not in COMPONENTS:
            _fail(f"tier cell {province}: unknown component {component!r}")
        if tier not in TIERS:
            _fail(f"tier cell {province}/{component}: unknown tier {tier!r}")
        key = (province, component)
        if key in seen:
            _fail(f"duplicate tier cell: {province}/{component}")
        seen.add(key)
        source_id = _text(row, "source_id", f"tier cell {province}/{component}")
        source = sources.get(source_id) or {}
        assumption_ids = [
            value.strip()
            for value in row.get("assumption_ids", "").split(";")
            if value.strip()
        ]
        by_province[province].append(
            {
                "component": component,
                "tier": tier,
                "tier_score": int(
                    _number(row, "tier_score", f"tier cell {province}/{component}")
                ),
                "basis": _text(
                    row, "component_basis", f"tier cell {province}/{component}"
                ),
                "source_key": source_id,
                "source_title": source.get("title") or source_id,
                "source_publication_date": source.get("publication_date") or "",
                "source_url": source.get("url") or "",
                "evidence_tier": row.get("source_evidence_tier", "").strip(),
                "donor_province": row.get("donor_province", "").strip(),
                "assumption_ids": assumption_ids,
                "direct_evidence": _boolean(
                    row, "direct_evidence", f"tier cell {province}/{component}"
                ),
                "proxy_model": _boolean(
                    row, "proxy_model", f"tier cell {province}/{component}"
                ),
                "donor_or_assumption": _boolean(
                    row, "donor_or_assumption", f"tier cell {province}/{component}"
                ),
                "notes": row.get("notes", "").strip(),
            }
        )
    for province, cells in by_province.items():
        components = [cell["component"] for cell in cells]
        if sorted(components) != sorted(COMPONENTS):
            _fail(f"tier cells for {province} do not cover all five components")
        cells.sort(key=lambda item: item["component"])
    return dict(by_province), {"cell_count": len(rows)}


def _stumpage_references(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Parse the eight provincial rates and five explicit BC fallbacks."""
    rows = _read_csv(path)
    if len(rows) != 13:
        _fail(f"stumpage references cover {len(rows)} jurisdictions, expected 13")
    references: dict[str, dict[str, Any]] = {}
    rate_count = 0
    fallback_count = 0
    for row in rows:
        province = _text(row, "province", "stumpage reference")
        if province in references:
            _fail(f"duplicate stumpage reference for {province}")
        is_rate = _boolean(
            row, "is_stumpage_or_dues_rate", f"stumpage reference {province}"
        )
        if _boolean(
            row, "is_delivered_price", f"stumpage reference {province}"
        ):
            _fail(f"stumpage reference {province} claims a delivered price")
        if _boolean(
            row, "is_observed_revenue", f"stumpage reference {province}"
        ):
            _fail(f"stumpage reference {province} claims observed revenue")
        if is_rate:
            rate_count += 1
            low = _number(row, "rate_low_cad_m3", f"stumpage reference {province}")
            base = _number(
                row, "rate_base_cad_m3", f"stumpage reference {province}"
            )
            high = _number(
                row, "rate_high_cad_m3", f"stumpage reference {province}"
            )
            if not low <= base <= high:
                _fail(
                    f"stumpage reference {province}: low/base/high are unordered"
                )
            if row.get("fallback_donor", "").strip().lower() not in {
                "",
                "false",
            }:
                _fail(f"stumpage reference {province} mixes a rate and a fallback")
            reference = {
                "has_rate": True,
                "rate_low_cad_m3": _round(low, 4),
                "rate_base_cad_m3": _round(base, 4),
                "rate_high_cad_m3": _round(high, 4),
                "unit": _text(row, "unit", f"stumpage reference {province}"),
                "rate_type": _text(
                    row, "rate_type", f"stumpage reference {province}"
                ),
                "rate_basis": _text(
                    row, "rate_basis", f"stumpage reference {province}"
                ),
                "published_range": row.get("published_range", "").strip(),
                "year": _text(
                    row,
                    "source_publication_date",
                    f"stumpage reference {province}",
                ),
                "source_key": _text(
                    row, "source_key", f"stumpage reference {province}"
                ),
                "source_title": _text(
                    row, "source_title", f"stumpage reference {province}"
                ),
                "source_url": row.get("source_url", "").strip(),
                "fallback_donor": "",
                "notes": row.get("notes", "").strip(),
                "is_stumpage_or_dues_rate": True,
                "is_delivered_price": False,
                "is_observed_revenue": False,
            }
        else:
            fallback_count += 1
            if any(
                row.get(field, "").strip()
                for field in (
                    "rate_low_cad_m3",
                    "rate_base_cad_m3",
                    "rate_high_cad_m3",
                )
            ):
                _fail(f"stumpage fallback {province} asserts a numeric rate")
            fallback_donor = _text(
                row, "fallback_donor", f"stumpage reference {province}"
            )
            reference = {
                "has_rate": False,
                "rate_low_cad_m3": None,
                "rate_base_cad_m3": None,
                "rate_high_cad_m3": None,
                "unit": row.get("unit", "").strip(),
                "rate_type": _text(
                    row, "rate_type", f"stumpage reference {province}"
                ),
                "rate_basis": _text(
                    row, "rate_basis", f"stumpage reference {province}"
                ),
                "published_range": row.get("published_range", "").strip(),
                "year": "",
                "source_key": "",
                "source_title": "",
                "source_url": "",
                "fallback_donor": fallback_donor,
                "notes": row.get("notes", "").strip(),
                "is_stumpage_or_dues_rate": False,
                "is_delivered_price": False,
                "is_observed_revenue": False,
            }
        references[province] = reference
    if (rate_count, fallback_count) != (8, 5):
        _fail(
            "stumpage references must contain 8 provincial rates and 5 "
            f"fallbacks, got {rate_count}/{fallback_count}"
        )
    return references, {
        "row_count": len(rows),
        "rate_count": rate_count,
        "fallback_count": fallback_count,
    }


def _replacement_queue(
    path: Path, provinces: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Attach a replacement priority to each province without ranking margins."""
    rows = _read_csv(path)
    if len(rows) != 60:
        _fail(f"replacement queue has {len(rows)} rows, expected 60")
    seen_cells: set[str] = set()
    by_province: dict[str, list[dict[str, str]]] = defaultdict(list)
    priority_counts: Counter[str] = Counter()
    queue_status: str | None = None
    for row in rows:
        province = _text(row, "province", "replacement queue")
        cell_id = _text(row, "coverage_cell_id", "replacement queue")
        priority = _text(row, "priority", "replacement queue")
        if cell_id in seen_cells:
            _fail(f"duplicate replacement queue cell: {cell_id}")
        seen_cells.add(cell_id)
        if priority not in {"P1", "P2", "P3", "P4"}:
            _fail(f"replacement queue {cell_id}: unknown priority {priority!r}")
        status = _text(row, "queue_status", "replacement queue")
        if queue_status is None:
            queue_status = status
        elif status != queue_status:
            _fail("replacement queue mixes queue statuses")
        priority_counts[priority] += 1
        by_province[province].append(row)
    if set(by_province) != set(provinces):
        _fail("replacement queue jurisdictions do not match the 13 provinces")
    quoted_status = queue_status or "open_required_source_not_invented"

    payload: dict[str, dict[str, Any]] = {}
    for province in provinces:
        province_rows = sorted(
            by_province[province],
            key=lambda row: _number(row, "queue_rank", f"replacement queue {province}"),
        )
        priorities = [row["priority"] for row in province_rows]
        top_priority = min(priorities, key=lambda value: int(value[1:]))
        components: list[str] = []
        cells: list[dict[str, Any]] = []
        for row in province_rows:
            component = _text(row, "component", f"replacement queue {province}")
            if component not in components:
                components.append(component)
            cells.append(
                {
                    "component": component,
                    "priority": row["priority"],
                    "required_source_category": _text(
                        row, "required_source_category", "replacement queue"
                    ),
                    "replacement_action": _text(
                        row, "replacement_action", "replacement queue"
                    ),
                    "current_source_id": row.get("current_source_id", "").strip(),
                    "donor_province": row.get("donor_province", "").strip(),
                    "coverage_cell_id": row["coverage_cell_id"],
                }
            )
        first = cells[0]
        payload[province] = {
            "priority": top_priority,
            "open_cell_count": len(cells),
            "components": components,
            "first_action": first["replacement_action"],
            "first_component": first["component"],
            "queue_status": quoted_status,
            "cells": cells,
        }
    return payload, {
        "row_count": len(rows),
        "province_count": len(by_province),
        "priority_counts": dict(sorted(priority_counts.items())),
        "queue_status": quoted_status,
    }


def _assumptions(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_csv(path)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        assumption_id = _text(row, "assumption_id", "assumptions")
        if assumption_id in result:
            _fail(f"duplicate assumption id: {assumption_id}")
        result[assumption_id] = {
            "id": assumption_id,
            "category": _text(row, "category", f"assumption {assumption_id}"),
            "applies_to": row.get("applies_to", "").strip(),
            "rule": _text(row, "rule", f"assumption {assumption_id}"),
            "notes": row.get("notes", "").strip(),
        }
    return result


def _validate_labels(rows: list[dict[str, str]], label: str) -> None:
    for row in rows:
        evidence = row.get("evidence", "").strip()
        if evidence and evidence != "model_estimate":
            _fail(f"{label}: unexpected evidence={evidence!r}")
        for field in ("observed", "policy_grade", "combined_national_net_value"):
            if field in row and row[field].strip().lower() not in {"", "false"}:
                _fail(f"{label}: {field} must be false")


def build_export(
    source_dir: Path = SOURCE_DIR,
    tier_source: Path = TIER_SOURCE,
    *,
    check_deletions: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the national UI payload and manifest from committed sources."""
    if check_deletions:
        assert_no_unexpected_deletions(REPO_ROOT)

    summary = _read_json(source_dir / SUMMARY_JSON)
    margin_rows = _read_csv(source_dir / MARGIN_CSV)
    summary_rows = _read_csv(source_dir / SUMMARY_CSV)
    comparison_rows = _read_csv(source_dir / COMPARISON_CSV)
    tier_rows = _read_csv(source_dir / TIER_CSV)
    tier_cell_rows = _read_csv(tier_source)
    assumptions_rows = _read_csv(source_dir / "assumptions_donor_register.csv")
    source_rows = _read_csv(source_dir / "sources.csv")
    stumpage_rows = _read_csv(STUMPAGE_SOURCE)
    queue_rows = _read_csv(QUEUE_SOURCE)
    allocation_rows = _read_csv(ALLOCATION_SOURCE)
    tier_summary = _read_json(TIER_SUMMARY_JSON)

    if summary.get("model_id") != MODEL_ID:
        _fail(f"unexpected model_id {summary.get('model_id')!r}")
    coverage = summary.get("coverage") or {}
    provinces = list(coverage.get("provinces") or [])
    if len(provinces) != 13 or len(set(provinces)) != 13:
        _fail("summary coverage does not list 13 unique jurisdictions")
    if coverage.get("missing") != [] or coverage.get("not_estimated_count") != 0:
        _fail("summary coverage reports missing or not-estimated jurisdictions")
    if coverage.get("margin_rows") != 78 or coverage.get("summary_rows") != 28:
        _fail("summary coverage row counts changed")

    decision = summary.get("decision") or {}
    base_scenario = decision.get("base_scenario")
    sensitivity_scenario = decision.get("sensitivity_scenario")
    if (base_scenario, sensitivity_scenario) != SCENARIO_KEYS:
        _fail(
            "expected donor_flat as base and stumpage_scaled as sensitivity, "
            f"got {base_scenario!r}/{sensitivity_scenario!r}"
        )
    for key in (
        "stumpage_scaled_observed",
        "stumpage_scaled_policy_grade",
    ):
        if decision.get(key) is not False:
            _fail(f"decision.{key} must be false")

    _validate_labels(margin_rows, "margin estimates")
    _validate_labels(summary_rows, "margin summary")
    _validate_labels(comparison_rows, "scenario comparison")
    _validate_labels(tier_rows, "tier coverage")
    _validate_labels(tier_cell_rows, "tier coverage audit")
    _validate_labels(stumpage_rows, "stumpage references")
    _validate_labels(queue_rows, "replacement queue")
    _validate_labels(allocation_rows, "provincial allocation")
    _validate_labels(assumptions_rows, "assumptions")
    _validate_labels(source_rows, "sources")

    sources_by_key = _source_map(source_dir / "sources.csv")
    tier_cells_by_province, tier_cell_meta = _tier_cells(
        tier_cell_rows, sources_by_key
    )
    stumpage_by_province, stumpage_meta = _stumpage_references(STUMPAGE_SOURCE)
    queue_by_province, queue_meta = _replacement_queue(
        QUEUE_SOURCE, provinces
    )
    assumptions_by_id = _assumptions(
        source_dir / "assumptions_donor_register.csv"
    )
    allocation_by_province: dict[str, float] = {}
    for row in allocation_rows:
        province = _text(row, "province", "provincial allocation")
        if province in allocation_by_province:
            _fail(f"duplicate provincial allocation for {province}")
        allocation_by_province[province] = _number(
            row, "harvest_m3", f"provincial allocation {province}"
        )
    if set(allocation_by_province) != set(provinces):
        _fail("provincial allocation does not match the 13 summary provinces")

    estimates = {
        (row["price_scenario"], row["province"], row["scenario_id"]): row
        for row in margin_rows
    }
    if len(estimates) != 78:
        _fail(f"margin estimates have {len(estimates)} rows, expected 78")
    summary_by_key = _scenario_rows(summary_rows, row_type="province")
    if len(summary_by_key) != 26:
        _fail(f"margin summary has {len(summary_by_key)} province rows, expected 26")
    comparison_by_province = {
        row["province"]: row
        for row in comparison_rows
        if row["province"] != "NATIONAL"
    }
    if len(comparison_by_province) != 13:
        _fail(
            "scenario comparison does not have one row per jurisdiction"
        )

    scenarios_payload = summary.get("price_scenarios") or {}
    jurisdictions: list[dict[str, Any]] = []
    for province in provinces:
        scenario_values: dict[str, Any] = {}
        first_row: dict[str, str] | None = None
        for scenario in SCENARIO_KEYS:
            key = (scenario, province)
            row = summary_by_key.get(key)
            if row is None:
                _fail(f"missing {scenario} summary row for {province}")
            scenario_estimates = {
                case: estimates.get((scenario, province, case))
                for case in ("low", "base", "high")
            }
            if any(item is None for item in scenario_estimates.values()):
                _fail(f"missing {scenario} estimate rows for {province}")
            first_estimate = scenario_estimates["base"]
            if row.get("province_name") != first_estimate.get("province_name"):
                _fail(f"{scenario}/{province}: province_name mismatch")
            low = _number(row, "margin_low_cad_m3", f"{scenario}/{province}")
            base = _number(row, "margin_base_cad_m3", f"{scenario}/{province}")
            high = _number(row, "margin_high_cad_m3", f"{scenario}/{province}")
            if not low <= base <= high:
                _fail(f"{scenario}/{province}: low/base/high are not ordered")
            for case, expected in (
                ("low", low),
                ("base", base),
                ("high", high),
            ):
                actual = _number(
                    scenario_estimates[case],
                    "margin_cad_m3",
                    f"{scenario}/{province}/{case}",
                )
                if abs(actual - expected) > 1e-6:
                    _fail(
                        f"{scenario}/{province}/{case}: summary and estimate "
                        "CSV disagree"
                    )
            price_tier = _text(row, "price_tier", f"{scenario}/{province}")
            cost_tier = _text(row, "cost_tier", f"{scenario}/{province}")
            scenario_values[scenario] = {
                "low_cad_m3": _round(low, 4),
                "base_cad_m3": _round(base, 4),
                "high_cad_m3": _round(high, 4),
                "price_tier": price_tier,
                "cost_tier": cost_tier,
                "price_basis": _text(
                    row, "price_basis", f"{scenario}/{province}"
                ),
                "stumpage_rate_type": _text(
                    row, "stumpage_rate_type", f"{scenario}/{province}"
                ),
            }
            if first_row is None:
                first_row = row
        if first_row is None:
            _fail(f"no rows for {province}")

        comparison = comparison_by_province[province]
        donor_row = scenario_values[base_scenario]
        scaled_row = scenario_values[sensitivity_scenario]
        delta = _round(
            _number(
                comparison,
                "scaled_minus_donor_flat_cad_m3",
                f"comparison/{province}",
            ),
            4,
        )
        expected_delta = scaled_row["base_cad_m3"] - donor_row["base_cad_m3"]
        if abs(delta - expected_delta) > 1e-3:
            _fail(f"comparison/{province}: delta does not match the two margins")
        if _number(
            comparison,
            "v2_1_donor_flat_margin_base_cad_m3",
            f"comparison/{province}",
        ) != donor_row["base_cad_m3"]:
            _fail(f"comparison/{province}: base margin mismatch")
        if _number(
            comparison,
            "v2_1_stumpage_scaled_margin_base_cad_m3",
            f"comparison/{province}",
        ) != scaled_row["base_cad_m3"]:
            _fail(f"comparison/{province}: sensitivity margin mismatch")
        if _boolean(
            comparison,
            "dues_comparable_to_delivered_price",
            f"comparison/{province}",
        ):
            _fail(f"comparison/{province}: dues must not be a delivered price")
        if comparison.get("comparison_status") != (
            "method_change_not_observed_market_change"
        ):
            _fail(f"comparison/{province}: unexpected comparison status")

        jurisdictions.append(
            {
                "province": province,
                "province_name": first_row["province_name"],
                "allocation_volume_m3": _round(
                    _number(first_row, "allocation_volume_m3", province), 1
                ),
                "volume_share": _round(
                    _number(first_row, "volume_share", province), 8
                ),
                "volume_reference_basis": _text(
                    first_row, "volume_reference_basis", province
                ),
                "donor_flat": scenario_values[base_scenario],
                "stumpage_scaled": scenario_values[sensitivity_scenario],
                "scaled_minus_donor_flat_cad_m3": delta,
                "stumpage_rate_type": _text(
                    comparison, "stumpage_rate_type", f"comparison/{province}"
                ),
                "dues_comparable_to_delivered_price": False,
                "comparison_status": "method_change_not_observed_market_change",
            }
        )

    if len(jurisdictions) != 13:
        _fail("jurisdiction table is incomplete")

    direct_jurisdictions: list[str] = []
    donor_only_jurisdictions: list[str] = []
    donor_price_jurisdictions: list[str] = []
    for jurisdiction in jurisdictions:
        province = jurisdiction["province"]
        cells = tier_cells_by_province.get(province)
        if not cells or len(cells) != len(COMPONENTS):
            _fail(f"tier cells are incomplete for {province}")
        mix = Counter(cell["tier"] for cell in cells)
        direct_cell_count = mix.get("reported_average", 0)
        proxy_cell_count = mix.get("proxy_model", 0)
        donor_cell_count = mix.get("donor_proxy", 0)
        assumption_cell_count = mix.get("assumption", 0)
        donor_only = direct_cell_count == 0 and proxy_cell_count == 0
        price_cell = next(
            cell for cell in cells if cell["component"] == "price"
        )
        price_source = sources_by_key.get(price_cell["source_key"]) or {}
        assumption_ids: list[str] = []
        for cell in cells:
            for assumption_id in cell["assumption_ids"]:
                if assumption_id not in assumption_ids:
                    assumption_ids.append(assumption_id)
        allocated = allocation_by_province[province]
        if abs(allocated - jurisdiction["allocation_volume_m3"]) > 0.2:
            _fail(f"allocation volume mismatch for {province}")
        jurisdiction.update(
            {
                "price_tier": jurisdiction["donor_flat"]["price_tier"],
                "cost_tier": jurisdiction["donor_flat"]["cost_tier"],
                "price_source": {
                    "source_key": price_cell["source_key"],
                    "title": price_source.get("title")
                    or price_cell["source_title"],
                    "publication_date": price_source.get("publication_date")
                    or "",
                    "evidence_tier": price_cell["tier"],
                    "is_direct": price_cell["tier"] == "reported_average",
                    "donor_province": price_cell["donor_province"],
                    "source_url": price_source.get("url") or "",
                },
                "evidence_mix": {
                    tier: mix.get(tier, 0) for tier in TIERS
                },
                "direct_cell_count": direct_cell_count,
                "proxy_cell_count": proxy_cell_count,
                "donor_cell_count": donor_cell_count,
                "assumption_cell_count": assumption_cell_count,
                "donor_only": donor_only,
                "status": (
                    "provisional_donor" if donor_only else "partial_direct"
                ),
                "status_label": (
                    "provisional donor" if donor_only else "partial direct"
                ),
                "comparison_status": (
                    "not_ranked_donor_only"
                    if donor_only
                    else "not_ranked_partial_direct"
                ),
                "ranked": False,
                "stumpage_reference": stumpage_by_province[province],
                "replacement": queue_by_province[province],
                "components": cells,
                "assumption_ids": assumption_ids,
            }
        )
        if direct_cell_count:
            direct_jurisdictions.append(province)
        if donor_only:
            donor_only_jurisdictions.append(province)
        if jurisdiction["price_tier"] == "donor_proxy":
            donor_price_jurisdictions.append(province)

    if len(direct_jurisdictions) != 2:
        _fail("expected exactly two jurisdictions with direct/reported cells")
    if len(donor_only_jurisdictions) != 11:
        _fail("expected exactly eleven donor-only jurisdictions")
    if len(donor_price_jurisdictions) != 12:
        _fail("expected exactly twelve donor-price jurisdictions")

    national_rows = [
        row for row in summary_rows if row.get("row_type") == "national"
    ]
    if len(national_rows) != 2:
        _fail("summary does not contain exactly two national rows")
    national_by_scenario = {
        row["price_scenario"]: row for row in national_rows
    }
    if set(national_by_scenario) != set(SCENARIO_KEYS):
        _fail("national rows do not cover both scenarios")
    diagnostic = summary.get("national_diagnostic") or {}
    if diagnostic.get("label") != "model_estimate_not_policy":
        _fail("national diagnostic label changed")
    if diagnostic.get("combined_national_net_value") is not False:
        _fail("national diagnostic must not be a combined net value")
    diagnostic_allocation = _number(
        national_rows[0], "allocation_volume_m3", "national"
    )
    if abs(
        float(diagnostic.get("allocation_volume_m3", math.nan))
        - diagnostic_allocation
    ) > 0.1:
        _fail("national diagnostic allocation volume diverges from the CSV")

    national_payload: dict[str, Any] = {
        "allocation_volume_m3": _round(
            _number(national_rows[0], "allocation_volume_m3", "national"), 1
        ),
        "label": diagnostic["label"],
        "combined_national_net_value": False,
        "not_decision_grade": True,
        "display_role": "diagnostic_not_headline",
        "display_position": "after_evidence_coverage_and_province_ledgers",
        "ranking_performed": False,
        "warning": (
            "National margins are volume-weighted model diagnostics shown last. "
            "They are not observed, not policy grade, not a combined national "
            "net value, and not a comparable jurisdictional ranking."
        ),
        "volume_weighted": True,
    }
    for scenario in SCENARIO_KEYS:
        row = national_by_scenario[scenario]
        low = _number(row, "margin_low_cad_m3", f"national/{scenario}")
        base = _number(row, "margin_base_cad_m3", f"national/{scenario}")
        high = _number(row, "margin_high_cad_m3", f"national/{scenario}")
        if not low <= base <= high:
            _fail(f"national/{scenario}: low/base/high are not ordered")
        source_diagnostic = (
            diagnostic.get("donor_flat")
            if scenario == base_scenario
            else diagnostic.get("stumpage_scaled_sensitivity")
        ) or {}
        if abs(float(source_diagnostic.get("margin_base_cad_m3", math.nan)) - base) > 1e-6:
            _fail(f"national/{scenario}: summary.json diagnostic diverges")
        national_payload[scenario] = {
            "scenario_role": (
                "base" if scenario == base_scenario else "sensitivity"
            ),
            "low_cad_m3": _round(low, 4),
            "base_cad_m3": _round(base, 4),
            "high_cad_m3": _round(high, 4),
        }

    national_comparison = next(
        (
            row
            for row in comparison_rows
            if row.get("province") == "NATIONAL"
        ),
        None,
    )
    if national_comparison is None:
        _fail("scenario comparison is missing the national row")
    national_delta = _round(
        _number(
            national_comparison,
            "scaled_minus_donor_flat_cad_m3",
            "comparison/national",
        ),
        4,
    )
    expected_national_delta = (
        national_payload[sensitivity_scenario]["base_cad_m3"]
        - national_payload[base_scenario]["base_cad_m3"]
    )
    if abs(national_delta - expected_national_delta) > 1e-3:
        _fail("national comparison delta does not match the diagnostic margins")

    tier_audit = _tier_coverage_audit(tier_source)
    price_tier_counts = _tier_counts(tier_rows, "price_tier_count")
    cost_tier_counts = _tier_counts(tier_rows, "cost_tier_count")
    if set(price_tier_counts) != set(SCENARIO_KEYS):
        _fail("price-tier counts do not cover both scenarios")
    for scenario, expected in (summary.get("tier_coverage") or {}).get(
        "price_tier_counts", {}
    ).items():
        if price_tier_counts.get(scenario) != expected:
            _fail(f"price-tier counts diverge for {scenario}")
    scenario_totals = _scenario_total_rows(tier_rows)
    for scenario in SCENARIO_KEYS:
        total = scenario_totals.get(scenario)
        if total is None or int(_number(total, "province_count", "scenario total")) != 13:
            _fail(f"tier coverage does not total 13 jurisdictions for {scenario}")
    tier_audit["scenario_price_tiers"] = price_tier_counts
    tier_audit["scenario_cost_tiers"] = cost_tier_counts

    shares = tier_audit["volume_weighted_shares"]
    direct_share = shares["reported_average"]
    proxy_share = shares["proxy_model"]
    donor_share = shares["donor_proxy"]
    assumption_share = shares["assumption"]
    tier_summary_shares = (tier_summary.get("volume_weighted") or {}).get(
        "tier_share"
    ) or {}
    for tier, share in shares.items():
        source_share = tier_summary_shares.get(tier)
        if source_share is None or abs(float(source_share) - share) > 1e-8:
            _fail(f"tier coverage source diverges for {tier}")

    donor_only_pct = _round(donor_share * 100, 1)
    evidence_coverage = {
        "default_view": "evidence_coverage",
        "basis": "volume_weighted_five_component_cells",
        "weighting": (
            "allocation harvest volume from provincial_allocation/119m.csv; "
            "each province has five equally weighted components"
        ),
        "jurisdiction_count": len(jurisdictions),
        "cell_count": tier_audit["cell_count"],
        "component_count": len(COMPONENTS),
        "volume_weighted": {
            "direct_reported": {
                "share": direct_share,
                "pct": _round(direct_share * 100, 4),
                "cells": tier_audit["cell_counts"]["reported_average"],
                "label": "direct / reported",
            },
            "proxy": {
                "share": proxy_share,
                "pct": _round(proxy_share * 100, 4),
                "cells": tier_audit["cell_counts"]["proxy_model"],
                "label": "proxy model",
            },
            "donor": {
                "share": donor_share,
                "pct": _round(donor_share * 100, 4),
                "cells": tier_audit["cell_counts"]["donor_proxy"],
                "label": "donor proxy",
            },
            "assumption": {
                "share": assumption_share,
                "pct": _round(assumption_share * 100, 4),
                "cells": tier_audit["cell_counts"]["assumption"],
                "label": "assumption",
            },
        },
        "direct_jurisdictions": direct_jurisdictions,
        "donor_only_jurisdictions": donor_only_jurisdictions,
        "donor_price_jurisdictions": donor_price_jurisdictions,
        "direct_cell_count": tier_audit["cell_counts"]["reported_average"],
        "proxy_cell_count": tier_audit["cell_counts"]["proxy_model"],
        "donor_cell_count": tier_audit["cell_counts"]["donor_proxy"],
        "assumption_cell_count": tier_audit["cell_counts"]["assumption"],
        "direct_jurisdiction_count": len(direct_jurisdictions),
        "donor_only_count": len(donor_only_jurisdictions),
        "donor_price_count": len(donor_price_jurisdictions),
        "not_decision_grade": True,
        "warning": (
            f"{donor_only_pct:.1f}% of volume-weighted evidence is a donor "
            f"proxy. {len(donor_only_jurisdictions)} of 13 jurisdictions are "
            "donor-only and flagged provisional donor; "
            f"{len(donor_price_jurisdictions)} of 13 use a donor price. "
            "These margins are not decision-grade and are not ranked as "
            "comparable results."
        ),
    }
    ranking = {
        "performed": False,
        "ordering": "canonical_jurisdiction_order",
        "donor_only_excluded": True,
        "reason": (
            "Donor-only jurisdictions are provisional evidence rows. The UI "
            "does not sort or rank margins across jurisdictions."
        ),
    }

    labels = summary.get("labels") or {}
    if labels.get("evidence") != "model_estimate":
        _fail("summary evidence label changed")
    if labels.get("observed") is not False or labels.get("policy_grade") is not False:
        _fail("summary labels must remain unobserved and not policy grade")
    if labels.get("combined_national_net_value") is not False:
        _fail("summary must not claim a combined national net value")

    sources = [
        {
            "path": _display_path(source_dir / MARGIN_CSV),
            "rows": len(margin_rows),
        },
        {
            "path": _display_path(source_dir / SUMMARY_CSV),
            "rows": len(summary_rows),
        },
        {
            "path": _display_path(source_dir / COMPARISON_CSV),
            "rows": len(comparison_rows),
        },
        {
            "path": _display_path(source_dir / TIER_CSV),
            "rows": len(tier_rows),
        },
        {
            "path": _display_path(source_dir / SUMMARY_JSON),
            "rows": None,
        },
        {
            "path": _display_path(tier_source),
            "rows": tier_audit["cell_count"],
        },
        {
            "path": _display_path(source_dir / "assumptions_donor_register.csv"),
            "rows": len(assumptions_rows),
        },
        {
            "path": _display_path(source_dir / "sources.csv"),
            "rows": len(source_rows),
        },
        {
            "path": _display_path(STUMPAGE_SOURCE),
            "rows": len(stumpage_rows),
        },
        {
            "path": _display_path(QUEUE_SOURCE),
            "rows": len(queue_rows),
        },
        {
            "path": _display_path(ALLOCATION_SOURCE),
            "rows": len(allocation_rows),
        },
        {
            "path": _display_path(TIER_SUMMARY_JSON),
            "rows": None,
        },
    ]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_id": summary["model_id"],
        "artifact_version": summary.get("artifact_version"),
        "generated_utc": summary.get("generated_utc"),
        "status": summary.get("status"),
        "role": "evidence_coverage_with_national_diagnostic",
        "default_view": "evidence_coverage",
        "section_order": [
            "evidence_coverage",
            "province_ledger",
            "province_drilldown",
            "national_margin_diagnostic",
            "downloads",
        ],
        "base_scenario": base_scenario,
        "sensitivity_scenario": sensitivity_scenario,
        "jurisdiction_count": len(jurisdictions),
        "decision": {
            "base_scenario": base_scenario,
            "sensitivity_scenario": sensitivity_scenario,
            "stumpage_scaled_observed": False,
            "stumpage_scaled_policy_grade": False,
            "reason": decision.get("reason"),
        },
        "labels": {
            "evidence": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "not_decision_grade": True,
            "ranking_performed": False,
            "combined_national_net_value": False,
            "national_diagnostic_label": "model_estimate_not_policy",
            "strict_open": False,
            "forest_lp_input": False,
            "public_osecan_input": False,
        },
        "coverage": {
            "province_count": len(provinces),
            "provinces": provinces,
            "not_estimated_count": 0,
            "jurisdiction_rows": len(jurisdictions),
            "scenario_rows": len(margin_rows),
        },
        "evidence_coverage": evidence_coverage,
        "ranking": ranking,
        "replacement_queue": queue_meta,
        "stumpage_references": {
            "path": _display_path(STUMPAGE_SOURCE),
            "row_count": stumpage_meta["row_count"],
            "rate_count": stumpage_meta["rate_count"],
            "fallback_count": stumpage_meta["fallback_count"],
            "rate_provinces": [
                province
                for province in provinces
                if stumpage_by_province[province]["has_rate"]
            ],
            "fallback_provinces": [
                province
                for province in provinces
                if not stumpage_by_province[province]["has_rate"]
            ],
            "rates_are_charges_not_delivered_prices": True,
        },
        "model_assumptions": [
            assumptions_by_id[key] for key in sorted(assumptions_by_id)
        ],
        "national_diagnostic": national_payload,
        "scenarios": [
            {
                "id": scenario,
                "role": (
                    "base" if scenario == base_scenario else "sensitivity"
                ),
                "label": (
                    "Base · donor_flat"
                    if scenario == base_scenario
                    else "Sensitivity · stumpage_scaled"
                ),
                "is_base": scenario == base_scenario,
                "price_basis": (
                    (scenarios_payload.get(scenario) or {}).get("price_basis")
                ),
                "price_tier_counts": price_tier_counts.get(scenario, {}),
            }
            for scenario in SCENARIO_KEYS
        ],
        "jurisdictions": jurisdictions,
        "tier_coverage": tier_audit,
        "national_comparison": {
            "scaled_minus_donor_flat_cad_m3": national_delta,
            "dues_comparable_to_delivered_price": False,
            "comparison_status": national_comparison.get("comparison_status"),
            "notes": national_comparison.get("notes"),
        },
        "caveats": [
            (
                "Evidence coverage comes first. The default view is the "
                "direct/reported, proxy and donor mix, not a national margin."
            ),
            (
                "Donor-only jurisdictions are flagged provisional donor. Their "
                "margins are placeholders, not comparable provincial results, "
                "and the UI does not rank margins."
            ),
            (
                "National values are volume-weighted model diagnostics shown "
                "last, not a combined national net value."
            ),
            (
                "donor_flat is the base scenario. stumpage_scaled is the method "
                "sensitivity: the sensitivity, not the base; not observed and "
                "not policy grade."
            ),
            (
                "Stumpage, dues, royalty and standing-value rates are charges on "
                "standing timber, not delivered log prices. The scaled-minus-"
                "donor-flat delta is a method change, not an observed market change."
            ),
            (
                "No jurisdiction row is an observed transaction, policy-grade "
                "value, Forest LP input or public OSeCAN input."
            ),
        ],
        "non_claims": [
            "No combined national net value.",
            "Not observed; observed=false.",
            "Not policy grade; policy_grade=false.",
            "Not a Forest LP objective or constraint input.",
            "Not a public OSeCAN input.",
            "Not a legal AAC or a policy frontier.",
        ],
        "source_files": sources,
        "download": {
            "json": {
                "path": "economics/national.json",
                "label": "National economics JSON",
                "media_type": "application/json",
            },
            "manifest": {
                "path": "economics/national.manifest.json",
                "label": "National economics manifest",
                "media_type": "application/json",
            },
        },
    }

    payload_text = _serialize(payload)
    payload_record = {
        "path": "economics/national.json",
        "label": "National economics JSON",
        "bytes": len(payload_text.encode("utf-8")),
        "sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
    }
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "artifact_version": summary.get("artifact_version"),
        "generated_utc": summary.get("generated_utc"),
        "parent_commit": _git_commit(REPO_ROOT),
        "data_commit": _git_commit(REPO_ROOT / "data"),
        "files": [payload_record],
        "generated": [payload_record],
        "sources": [
            {
                "path": item["path"],
                "rows": item["rows"],
                "bytes": (REPO_ROOT / item["path"]).stat().st_size,
                "sha256": _sha256(REPO_ROOT / item["path"]),
            }
            for item in sources
        ],
        "validation": {
            "ok": True,
            "default_view": payload["default_view"],
            "jurisdiction_count": len(jurisdictions),
            "direct_reported_share": direct_share,
            "proxy_share": proxy_share,
            "donor_share": donor_share,
            "direct_cell_count": evidence_coverage["direct_cell_count"],
            "proxy_cell_count": evidence_coverage["proxy_cell_count"],
            "donor_cell_count": evidence_coverage["donor_cell_count"],
            "direct_jurisdiction_count": len(direct_jurisdictions),
            "donor_only_count": len(donor_only_jurisdictions),
            "donor_price_count": len(donor_price_jurisdictions),
            "stumpage_rate_count": stumpage_meta["rate_count"],
            "stumpage_fallback_count": stumpage_meta["fallback_count"],
            "replacement_queue_rows": queue_meta["row_count"],
            "ranking_performed": False,
            "not_decision_grade": True,
            "base_national_margin_cad_m3": national_payload[base_scenario][
                "base_cad_m3"
            ],
            "sensitivity_national_margin_cad_m3": national_payload[
                sensitivity_scenario
            ]["base_cad_m3"],
            "tier_cell_count": tier_audit["cell_count"],
            "tier_volume_shares": tier_audit["volume_weighted_shares"],
            "observed_claim": False,
            "policy_grade_claim": False,
            "combined_national_net_value": False,
            "forest_lp_input": False,
            "public_osecan_input": False,
            "deletions_checked": check_deletions,
        },
    }
    return payload, manifest


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _serialize(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def write_export(
    payload: dict[str, Any],
    manifest: dict[str, Any],
    out_json: Path = OUT_JSON,
    out_manifest: Path = OUT_MANIFEST,
) -> None:
    """Write the payload and manifest atomically."""
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload_text = _serialize(payload)
    manifest_text = _serialize(manifest)
    tmp_json = out_json.with_suffix(out_json.suffix + ".tmp")
    tmp_manifest = out_manifest.with_suffix(out_manifest.suffix + ".tmp")
    tmp_json.write_text(payload_text, encoding="utf-8")
    tmp_manifest.write_text(manifest_text, encoding="utf-8")
    tmp_json.replace(out_json)
    tmp_manifest.replace(out_manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export national economics v2.1 for the live UI."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=SOURCE_DIR,
        help="national_economics_v2_1 directory",
    )
    parser.add_argument(
        "--tier-source",
        type=Path,
        default=TIER_SOURCE,
        help="national_tier_coverage.csv path",
    )
    parser.add_argument("--out", type=Path, default=OUT_JSON)
    parser.add_argument("--manifest", type=Path, default=OUT_MANIFEST)
    args = parser.parse_args(argv)

    payload, manifest = build_export(args.source_dir, args.tier_source)
    write_export(payload, manifest, args.out, args.manifest)
    coverage = payload["evidence_coverage"]["volume_weighted"]
    print(
        "national economics export: "
        f"{payload['coverage']['jurisdiction_rows']} jurisdictions, "
        f"direct={coverage['direct_reported']['pct']:.4f}% "
        f"proxy={coverage['proxy']['pct']:.4f}% "
        f"donor={coverage['donor']['pct']:.4f}%, "
        f"donor_only={payload['evidence_coverage']['donor_only_count']}, "
        f"sha256={manifest['generated'][0]['sha256'][:16]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
