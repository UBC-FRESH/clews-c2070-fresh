"""Export national economics v2.3 into the read-only live UI payload.

The export is evidence-first: the default view is provincial evidence coverage,
not a national margin headline.  It is derived only from committed CSVs and
``summary.json``.  Version 2.3 changes the cost basis for ten jurisdictions to
the Statistics Canada NAICS 11331 industry aggregate total-cost proxy.  The
aggregate is explicitly **not** direct and has no logging/haul breakdown.

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
    "assert_no_unexpected_deletions",
    "build_export",
    "main",
    "write_export",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
SOURCE_DIR = HARVEST_BASIS_DIR / "national_economics_v2_3"
V21_DIR = HARVEST_BASIS_DIR / "national_economics_v2_1"
STATCAN_DIR = HARVEST_BASIS_DIR / "statcan_logging_cost_proxy"
TIER_SOURCE = (
    HARVEST_BASIS_DIR
    / "national_tier_coverage"
    / "national_tier_coverage.csv"
)
TIER_SUMMARY_JSON = (
    HARVEST_BASIS_DIR
    / "national_tier_coverage"
    / "national_tier_summary.json"
)
PRICE_SOURCES = V21_DIR / "sources.csv"
STUMPAGE_SOURCE = (
    HARVEST_BASIS_DIR
    / "national_economics_v2"
    / "provincial_stumpage_references.csv"
)
ALLOCATION_SOURCE = HARVEST_BASIS_DIR / "provincial_allocation/119m.csv"
MARGIN_TOTALS_DIR = HARVEST_BASIS_DIR / "national_margin_totals"
MARGIN_TOTALS_CSV = MARGIN_TOTALS_DIR / "province_margin_totals.csv"
MARGIN_TOTALS_JSON = MARGIN_TOTALS_DIR / "national_margin_totals.json"
NET_VALUE_DIR = HARVEST_BASIS_DIR / "forest_lp_net_value_sensitivity"
NET_VALUE_ALLOCATION_CSV = NET_VALUE_DIR / "allocation_net_value.csv"
NET_VALUE_GROUP_CSV = NET_VALUE_DIR / "allocation_net_value_summary.csv"
NET_VALUE_NATIONAL_CSV = NET_VALUE_DIR / "national_net_value_summary.csv"
NET_VALUE_SUMMARY_JSON = NET_VALUE_DIR / "summary.json"
ALLOCATION_LP_DIR = HARVEST_BASIS_DIR / "allocation_net_value_lp"
ALLOCATION_LP_CSV = ALLOCATION_LP_DIR / "allocation_net_value_lp.csv"
ALLOCATION_LP_DELTA_CSV = ALLOCATION_LP_DIR / "allocation_delta.csv"
ALLOCATION_LP_SUMMARY_JSON = ALLOCATION_LP_DIR / "summary.json"
OUT_JSON = REPO_ROOT / "ui/public/data/economics/national.json"
OUT_MANIFEST = REPO_ROOT / "ui/public/data/economics/national.manifest.json"

SCHEMA_VERSION = "economics/national/3"
MANIFEST_SCHEMA_VERSION = "economics/national-manifest/3"
MODEL_ID = "national_economics_v2_3"

COMPONENTS = ("price", "logging", "haul", "road", "silviculture")
PRICE_TIERS = ("reported_average", "proxy_model", "donor_proxy", "assumption")
SCENARIO_KEYS = ("donor_flat", "stumpage_scaled")
VOLUME_BASIS_OBSERVED = "observed_2024_m3"
VOLUME_BASIS_119M = "119m_allocation_sensitivity"

COST_TIER_COMPONENT = "component_stack"
COST_TIER_AGGREGATE = "industry_aggregate_proxy"
COST_TIER_FALLBACK = "donor_fallback"
COST_TIERS = (COST_TIER_COMPONENT, COST_TIER_AGGREGATE, COST_TIER_FALLBACK)
COST_TIER_MAP = {
    "component_mix_partial_direct": COST_TIER_COMPONENT,
    "component_survey_aggregate": COST_TIER_COMPONENT,
    "industry_aggregate_proxy": COST_TIER_AGGREGATE,
    "donor_fallback_total": COST_TIER_FALLBACK,
}
COST_TIER_LABEL = {
    COST_TIER_COMPONENT: "component stack",
    COST_TIER_AGGREGATE: "provisional aggregate",
    COST_TIER_FALLBACK: "donor fallback",
}

AGGREGATE_CAVEAT = (
    "The StatCan NAICS 11331 total expense includes administration, "
    "depreciation, overhead and non-logging activity, plus any in-forest haul "
    "performed by logging establishments. It is not a logging-only or "
    "haul-only cost curve and no separate road, silviculture or haul add-on "
    "is applied."
)

MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
COMPARISON_CSV = "v2_2_v2_3_comparison.csv"
TIER_CSV = "tier_coverage.csv"
COST_INPUTS_CSV = "national_cost_inputs.csv"
SUMMARY_JSON = "summary.json"
STATCAN_CSV = "provincial_logging_cost_proxy.csv"
STATCAN_SOURCES_CSV = "sources.csv"

NET_VALUE_MODEL_ID = "forest_lp_net_value_sensitivity_v1"
NET_VALUE_SCHEMA_VERSION = "national/forest-lp-net-value-sensitivity/1"
NET_VALUE_ALLOCATION_CASES = ("100m", "119m", "150m")
NET_VALUE_SCENARIOS = ("donor_flat", "stumpage_scaled")
NET_VALUE_CAP_CASES = ("documented_ceiling", "intensity_1.0", "intensity_1.2")
NET_VALUE_EXPECTED_ALLOCATION_ROWS = 117
NET_VALUE_EXPECTED_GROUPS = 18
NET_VALUE_EXPECTED_NATIONAL_ROWS = 54
NET_VALUE_EXPECTED_INFEASIBLE_GROUPS = 4
# Documented-ceiling base net values, M CAD, from the committed summary.
NET_VALUE_EXPECTED_BASE_M_CAD = {
    ("100m", "donor_flat"): 4347.22634042173,
    ("100m", "stumpage_scaled"): -559.72283638965,
    ("119m", "donor_flat"): 5205.32874847936,
    ("119m", "stumpage_scaled"): -655.31912331146,
    ("150m", "donor_flat"): 6662.82394963578,
    ("150m", "stumpage_scaled"): -1194.8638226528499,
}

ALLOCATION_LP_MODEL_ID = "provincial_allocation_net_value_lp_v1"
ALLOCATION_LP_SCHEMA_VERSION = "national/provincial-allocation-net-value-lp/1"
ALLOCATION_LP_LEVELS = ("100m", "119m", "150m")
ALLOCATION_LP_SCENARIOS = ("donor_flat", "stumpage_scaled")
ALLOCATION_LP_PROVINCES = (
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
ALLOCATION_LP_EXPECTED_ALLOCATION_ROWS = 182
ALLOCATION_LP_EXPECTED_DELTA_ROWS = 182
ALLOCATION_LP_EXPECTED_FEASIBLE_CASES = 14
ALLOCATION_LP_EXPECTED_SUMMARY_CASES = 18
ALLOCATION_LP_EXPECTED_DOCUMENTED_ROWS = 6
ALLOCATION_LP_EXPECTED_PROVINCE_SHIFTS = 13
ALLOCATION_LP_SHIFT_LEVEL = "119m"


class NationalEconomicsUiError(RuntimeError):
    """Fail-closed export error."""


def _fail(message: str) -> None:
    raise NationalEconomicsUiError(message)


def _sha256_bytes(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _optional_number(
    row: dict[str, str], field: str, label: str
) -> float | None:
    raw = row.get(field, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as error:
        _fail(f"{label}: invalid {field}={raw!r}: {error}")
    if not math.isfinite(value):
        _fail(f"{label}: non-finite {field}")
    return value


def _assert_no_unexpected_d(
    rows: list[dict[str, str]], label: str
) -> None:
    """Fail closed if a source row carries an unexpected D quality flag."""
    for index, row in enumerate(rows, start=1):
        for field, value in row.items():
            if value is not None and str(value).strip() == "D":
                _fail(
                    f"{label}: unexpected D quality flag at row {index} "
                    f"field {field}"
                )


def _round(value: float, digits: int) -> float:
    return round(value, digits)


def _validate_labels(rows: list[dict[str, str]], label: str) -> None:
    for row in rows:
        evidence = row.get("evidence", "").strip()
        if evidence and evidence != "model_estimate":
            _fail(f"{label}: unexpected evidence={evidence!r}")
        for field in ("observed", "policy_grade", "combined_national_net_value"):
            if field in row and row[field].strip().lower() not in {"", "false"}:
                _fail(f"{label}: {field} must be false")


def _validate_statcan_d(
    statcan_rows: list[dict[str, str]], summary: dict[str, Any]
) -> dict[str, Any]:
    """Treat D as an acceptable flag, but fail on an unexpected D cell."""
    observed = {
        (row["province"], field)
        for row in statcan_rows
        for field, value in row.items()
        if field.endswith("_status") and value.strip() == "D"
    }
    d_status = summary.get("statcan_d_status") or {}
    allowed = {
        tuple(cell.split(":", 1))
        for cell in d_status.get("allowed_cells", [])
    }
    unexpected = sorted(observed - allowed)
    if d_status.get("unexpected_cells") not in (None, []):
        _fail(
            "v2.3 summary reports unexpected StatCan D cells: "
            f"{d_status['unexpected_cells']}"
        )
    if unexpected:
        _fail(f"unexpected StatCan D status cells: {unexpected}")
    return {
        "policy": "D is an acceptable-quality flag, not a deletion; unexpected D fails closed.",
        "allowed_cell_count": len(allowed),
        "observed_d_cell_count": len(observed),
        "unexpected_cells": [],
    }


def _cost_coverage(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Parse the v2.3 province-level cost-basis coverage table."""
    summaries = {
        (row.get("version", "").strip(), row.get("volume_basis", "").strip()): row
        for row in rows
        if row.get("row_type") == "coverage_summary"
    }
    if len(summaries) != 4:
        _fail("v2.3 tier coverage must contain four coverage_summary rows")

    def parse(version: str, volume_basis: str) -> dict[str, Any]:
        row = summaries.get((version, volume_basis))
        if row is None:
            _fail(f"missing {version}/{volume_basis} coverage summary")
        aggregate_cells = int(_number(row, "aggregate_proxy_cells", "coverage"))
        component_cells = int(_number(row, "component_cells", "coverage"))
        donor_proxy_cells = int(_number(row, "donor_proxy_cells", "coverage"))
        fallback_cells = int(_number(row, "donor_fallback_cells", "coverage"))
        if (
            aggregate_cells
            + component_cells
            + fallback_cells
            + donor_proxy_cells
            != 13
        ):
            _fail(f"{version}/{volume_basis}: cost cells do not total 13")
        shares = {
            COST_TIER_COMPONENT: _number(
                row, "component_volume_share", "coverage"
            ),
            COST_TIER_AGGREGATE: _number(
                row, "aggregate_proxy_volume_share", "coverage"
            ),
            COST_TIER_FALLBACK: _number(
                row, "donor_fallback_volume_share", "coverage"
            ),
            "donor_proxy": _number(
                row, "donor_proxy_volume_share", "coverage"
            ),
        }
        if abs(sum(shares.values()) - 1.0) > 1e-7:
            _fail(f"{version}/{volume_basis}: cost shares do not sum to 1")
        return {
            "aggregate_proxy_cells": aggregate_cells,
            "component_cells": component_cells,
            "donor_proxy_cells": donor_proxy_cells,
            "donor_fallback_cells": fallback_cells,
            "shares": {key: _round(value, 8) for key, value in shares.items()},
            "pct": {
                key: _round(value * 100, 4) for key, value in shares.items()
            },
        }

    after_observed = parse("v2_3", VOLUME_BASIS_OBSERVED)
    after_119m = parse("v2_3", VOLUME_BASIS_119M)
    before_observed = parse("v2_2", VOLUME_BASIS_OBSERVED)
    before_119m = parse("v2_2", VOLUME_BASIS_119M)

    upgrades = [
        {
            "province": _text(row, "province", "cost upgrade"),
            "tier_before": _text(row, "tier_before", "cost upgrade"),
            "tier_after": _text(row, "tier_after", "cost upgrade"),
        }
        for row in rows
        if row.get("row_type") == "cost_proxy_upgrade"
    ]
    if len(upgrades) != 10:
        _fail(f"expected 10 cost-proxy upgrades, got {len(upgrades)}")
    if after_observed["aggregate_proxy_cells"] != 10:
        _fail("expected ten industry-aggregate cost cells")
    if after_observed["donor_fallback_cells"] != 1:
        _fail("expected one donor-fallback cost cell")
    if after_observed["donor_proxy_cells"] != 0:
        _fail("v2.3 must not retain donor-proxy cost cells")
    return {
        "basis": "province_level_cost_basis_cells",
        "cell_count": 13,
        "primary_volume_basis": VOLUME_BASIS_OBSERVED,
        "sensitivity_volume_basis": VOLUME_BASIS_119M,
        "volume_bases": {
            VOLUME_BASIS_OBSERVED: after_observed,
            VOLUME_BASIS_119M: after_119m,
        },
        "before": {
            VOLUME_BASIS_OBSERVED: before_observed,
            VOLUME_BASIS_119M: before_119m,
        },
        "upgrade_count": len(upgrades),
        "upgrades": upgrades,
        "full_direct_coverage_claimed": False,
    }


def _tier_coverage_audit(path: Path) -> dict[str, Any]:
    """Audit the 65 five-component price/evidence cells unchanged from v2.1."""
    rows = _read_csv(path)
    provinces = {row.get("province", "").strip() for row in rows}
    components = {row.get("component", "").strip() for row in rows}
    tiers = {row.get("tier", "").strip() for row in rows}
    if len(provinces) != 13:
        _fail(f"tier audit covers {len(provinces)} provinces, expected 13")
    if components != set(COMPONENTS):
        _fail(f"tier audit components mismatch: {sorted(components)}")
    if tiers - set(PRICE_TIERS):
        _fail(f"tier audit has unknown tiers: {sorted(tiers - set(PRICE_TIERS))}")
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
    shares = {
        tier: _round(volume_by_tier.get(tier, 0.0) / total_cell_volume, 8)
        for tier in PRICE_TIERS
    }
    if abs(sum(shares.values()) - 1.0) > 1e-6:
        _fail("tier audit volume shares do not sum to 1")
    return {
        "basis": "volume_weighted_five_component_price_cells",
        "component_count": len(components),
        "province_count": len(provinces),
        "cell_count": len(rows),
        "volume_weighted_shares": shares,
        "volume_weighted_pct": {
            tier: _round(share * 100, 4) for tier, share in shares.items()
        },
        "cell_counts": {tier: cell_counts.get(tier, 0) for tier in PRICE_TIERS},
        "total_harvest_volume_m3": _round(
            total_cell_volume / len(COMPONENTS), 1
        ),
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
    """Build one price/evidence record per province x component cell."""
    by_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        province = _text(row, "province", "tier cell")
        component = _text(row, "component", "tier cell")
        tier = _text(row, "tier", "tier cell")
        if component not in COMPONENTS:
            _fail(f"tier cell {province}: unknown component {component!r}")
        if tier not in PRICE_TIERS:
            _fail(f"tier cell {province}/{component}: unknown tier {tier!r}")
        key = (province, component)
        if key in seen:
            _fail(f"duplicate tier cell: {province}/{component}")
        seen.add(key)
        source_id = _text(row, "source_id", f"tier cell {province}/{component}")
        source = sources.get(source_id) or {}
        by_province[province].append(
            {
                "component": component,
                "tier": tier,
                "basis": _text(
                    row, "component_basis", f"tier cell {province}/{component}"
                ),
                "source_key": source_id,
                "source_title": source.get("title") or source_id,
                "source_publication_date": source.get("publication_date") or "",
                "source_url": source.get("url") or "",
                "donor_province": row.get("donor_province", "").strip(),
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
        is_rate = _boolean(
            row, "is_stumpage_or_dues_rate", f"stumpage reference {province}"
        )
        if _boolean(row, "is_delivered_price", f"stumpage {province}"):
            _fail(f"stumpage reference {province} claims a delivered price")
        if _boolean(row, "is_observed_revenue", f"stumpage {province}"):
            _fail(f"stumpage reference {province} claims observed revenue")
        if is_rate:
            rate_count += 1
            low = _number(row, "rate_low_cad_m3", f"stumpage {province}")
            base = _number(row, "rate_base_cad_m3", f"stumpage {province}")
            high = _number(row, "rate_high_cad_m3", f"stumpage {province}")
            if not low <= base <= high:
                _fail(f"stumpage {province}: low/base/high are unordered")
            references[province] = {
                "has_rate": True,
                "rate_low_cad_m3": _round(low, 4),
                "rate_base_cad_m3": _round(base, 4),
                "rate_high_cad_m3": _round(high, 4),
                "year": _text(row, "source_publication_date", "stumpage"),
                "rate_type": _text(row, "rate_type", "stumpage"),
                "source_title": _text(row, "source_title", "stumpage"),
                "source_key": _text(row, "source_key", "stumpage"),
                "source_url": row.get("source_url", "").strip(),
                "fallback_donor": "",
                "notes": row.get("notes", "").strip(),
                "is_delivered_price": False,
            }
        else:
            fallback_count += 1
            references[province] = {
                "has_rate": False,
                "rate_low_cad_m3": None,
                "rate_base_cad_m3": None,
                "rate_high_cad_m3": None,
                "year": "",
                "rate_type": _text(row, "rate_type", "stumpage"),
                "source_title": "",
                "source_key": "",
                "source_url": "",
                "fallback_donor": _text(row, "fallback_donor", "stumpage"),
                "notes": row.get("notes", "").strip(),
                "is_delivered_price": False,
            }
    if (rate_count, fallback_count) != (8, 5):
        _fail(
            "stumpage references must contain 8 rates and 5 fallbacks, "
            f"got {rate_count}/{fallback_count}"
        )
    return references, {
        "row_count": len(rows),
        "rate_count": rate_count,
        "fallback_count": fallback_count,
    }


def _cost_inputs(
    rows: list[dict[str, str]], summary_rows: list[dict[str, str]]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Build one province-level cost record plus component details."""
    if len(rows) != 18:
        _fail(f"cost inputs have {len(rows)} rows, expected 18")
    summary_cost = {
        (row["price_scenario"], row["province"]): row
        for row in summary_rows
        if row.get("row_type") == "province"
    }
    by_province: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        province = _text(row, "province", "cost input")
        by_province[province].append(row)

    result: dict[str, dict[str, Any]] = {}
    tier_counts: Counter[str] = Counter()
    for province, province_rows in by_province.items():
        raw_tiers = {row.get("cost_tier", "").strip() for row in province_rows}
        if len(raw_tiers) != 1:
            _fail(f"cost inputs for {province} mix cost tiers: {raw_tiers}")
        raw_tier = next(iter(raw_tiers))
        tier = COST_TIER_MAP.get(raw_tier)
        if tier is None:
            _fail(f"cost input {province}: unknown cost tier {raw_tier!r}")
        tier_counts[tier] += 1

        low = sum(_number(row, "low_cad_m3", f"cost {province}") for row in province_rows)
        base = sum(_number(row, "base_cad_m3", f"cost {province}") for row in province_rows)
        high = sum(_number(row, "high_cad_m3", f"cost {province}") for row in province_rows)
        if not low <= base <= high:
            _fail(f"cost inputs for {province}: low/base/high are unordered")

        donor_row = summary_cost.get(("donor_flat", province))
        if donor_row is None:
            _fail(f"missing donor_flat summary row for {province}")
        summary_base = _number(
            donor_row, "total_cost_base_cad_m3", f"summary cost {province}"
        )
        if abs(base - summary_base) > 1e-3:
            _fail(f"cost stack for {province} does not match the summary")

        components = [
            {
                "component": _text(row, "component", f"cost {province}"),
                "low_cad_m3": _round(_number(row, "low_cad_m3", "cost"), 4),
                "base_cad_m3": _round(_number(row, "base_cad_m3", "cost"), 4),
                "high_cad_m3": _round(_number(row, "high_cad_m3", "cost"), 4),
                "tier": _text(row, "tier", "cost"),
                "cost_tier": tier,
                "source_key": _text(row, "source_key", "cost"),
                "source": _text(row, "source", "cost"),
                "source_url": row.get("source_url", "").strip(),
                "source_year": _text(row, "source_year", "cost"),
                "includes_overhead_not_logging_haul_breakdown": _boolean(
                    row,
                    "includes_overhead_not_logging_haul_breakdown",
                    "cost",
                ),
                "notes": row.get("notes", "").strip(),
            }
            for row in province_rows
        ]
        first = components[0]
        if tier == COST_TIER_AGGREGATE:
            source = {
                "source_key": first["source_key"],
                "title": first["source"],
                "year": first["source_year"],
                "source_url": first["source_url"],
                "statcan_status": province_rows[0].get(
                    "statcan_status", ""
                ).strip(),
                "data_quality_flags": province_rows[0].get(
                    "statcan_data_quality_flags", ""
                ).strip(),
                "includes_overhead": True,
                "not_logging_haul_breakdown": True,
                "is_direct": False,
                "provisional_aggregate": True,
                "caveat": AGGREGATE_CAVEAT,
            }
        elif tier == COST_TIER_FALLBACK:
            source = {
                "source_key": first["source_key"],
                "title": first["source"],
                "year": first["source_year"],
                "source_url": first["source_url"],
                "statcan_status": province_rows[0].get(
                    "statcan_status", ""
                ).strip(),
                "data_quality_flags": "",
                "includes_overhead": False,
                "not_logging_haul_breakdown": False,
                "is_direct": False,
                "provisional_aggregate": False,
                "fallback_used": True,
                "caveat": (
                    "StatCan reports zero expenses and zero harvest for Nunavut, "
                    "so no CAD/m3 ratio exists. The explicit v2.2 donor-total "
                    "fallback is retained; no StatCan value is fabricated."
                ),
            }
        else:
            source = {
                "source_key": "bc_qc_component_stack",
                "title": (
                    "BC v2.2 component stack"
                    if province == "BC"
                    else "QC v2.2 component stack"
                ),
                "year": first["source_year"],
                "source_url": first["source_url"],
                "statcan_status": "",
                "data_quality_flags": "",
                "includes_overhead": False,
                "not_logging_haul_breakdown": False,
                "is_direct": False,
                "provisional_aggregate": False,
                "caveat": (
                    "Component cost stack retained from v2.2. It is not a "
                    "direct observed logging cost; component tiers and donors "
                    "remain visible below."
                ),
            }

        result[province] = {
            "tier": tier,
            "tier_source": raw_tier,
            "label": COST_TIER_LABEL[tier],
            "low_cad_m3": _round(low, 4),
            "base_cad_m3": _round(base, 4),
            "high_cad_m3": _round(high, 4),
            "source": source,
            "components": components,
        }
    if len(result) != 13:
        _fail(f"cost inputs cover {len(result)} jurisdictions, expected 13")
    if tier_counts[COST_TIER_AGGREGATE] != 10:
        _fail("expected ten industry-aggregate cost jurisdictions")
    if tier_counts[COST_TIER_COMPONENT] != 2:
        _fail("expected two component-stack cost jurisdictions")
    if tier_counts[COST_TIER_FALLBACK] != 1:
        _fail("expected one donor-fallback cost jurisdiction")
    return dict(result), {
        "row_count": len(rows),
        "province_count": len(result),
        "tier_counts": dict(sorted(tier_counts.items())),
    }


def _national_diagnostic(
    summary_rows: list[dict[str, str]], summary: dict[str, Any]
) -> dict[str, Any]:
    national_rows = [
        row for row in summary_rows if row.get("row_type") == "national"
    ]
    if len(national_rows) != 4:
        _fail("summary must contain four national rows: two scenarios x two bases")
    by_key = {
        (row["price_scenario"], row["volume_basis"]): row
        for row in national_rows
    }
    primary: dict[str, dict[str, float]] = {}
    sensitivity_119m: dict[str, dict[str, float]] = {}
    for scenario in SCENARIO_KEYS:
        for basis, target in (
            (VOLUME_BASIS_OBSERVED, primary),
            (VOLUME_BASIS_119M, sensitivity_119m),
        ):
            row = by_key.get((scenario, basis))
            if row is None:
                _fail(f"missing national {scenario}/{basis} row")
            low = _number(row, "margin_low_cad_m3", "national")
            base = _number(row, "margin_base_cad_m3", "national")
            high = _number(row, "margin_high_cad_m3", "national")
            if not low <= base <= high:
                _fail(f"national {scenario}/{basis}: unordered margins")
            target[scenario] = {
                "low_cad_m3": _round(low, 4),
                "base_cad_m3": _round(base, 4),
                "high_cad_m3": _round(high, 4),
            }

    source_diagnostic = summary.get("national_diagnostic") or {}
    if source_diagnostic.get("label") != "model_estimate_not_policy":
        _fail("national diagnostic label changed")
    if source_diagnostic.get("combined_national_net_value") is not False:
        _fail("national diagnostic must not be a combined net value")
    observed = (
        (source_diagnostic.get("donor_flat") or {}).get(
            VOLUME_BASIS_OBSERVED
        )
        or {}
    )
    if abs(
        float(observed.get("margin_base_cad_m3", math.nan))
        - primary["donor_flat"]["base_cad_m3"]
    ) > 1e-6:
        _fail("national diagnostic diverges from the summary CSV")
    scaled = (
        (source_diagnostic.get("stumpage_scaled_sensitivity") or {}).get(
            VOLUME_BASIS_OBSERVED
        )
        or {}
    )
    if abs(
        float(scaled.get("margin_base_cad_m3", math.nan))
        - primary["stumpage_scaled"]["base_cad_m3"]
    ) > 1e-6:
        _fail("national sensitivity diverges from the summary CSV")

    allocation = _round(
        _number(national_rows[0], "allocation_volume_m3", "national"), 1
    )
    return {
        "allocation_volume_m3": allocation,
        "observed_2024_volume_m3": _round(
            _number(national_rows[0], "observed_2024_volume_m3", "national"), 1
        ),
        "primary_volume_basis": VOLUME_BASIS_OBSERVED,
        "sensitivity_volume_basis": VOLUME_BASIS_119M,
        "label": "model_estimate_not_policy",
        "combined_national_net_value": False,
        "not_decision_grade": True,
        "display_role": "diagnostic_not_headline",
        "display_position": "after_evidence_coverage_and_province_ledgers",
        "ranking_performed": False,
        "volume_weighted": True,
        "warning": (
            "National margins are volume-weighted model diagnostics shown last. "
            "They are not observed, not policy grade, not a combined national "
            "net value, and not a comparable jurisdictional ranking."
        ),
        "donor_flat": {
            **primary["donor_flat"],
            "scenario_role": "base",
            "volume_basis": VOLUME_BASIS_OBSERVED,
        },
        "stumpage_scaled": {
            **primary["stumpage_scaled"],
            "scenario_role": "sensitivity",
            "volume_basis": VOLUME_BASIS_OBSERVED,
        },
        "sensitivity_119m": {
            "donor_flat": sensitivity_119m["donor_flat"],
            "stumpage_scaled": sensitivity_119m["stumpage_scaled"],
        },
    }


def _margin_totals(
    rows: list[dict[str, str]],
    payload: dict[str, Any],
    allocation_by_province: dict[str, float],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    """Validate and index the 119 Mm3 province total-margin ledger."""
    if payload.get("model_id") != "national_margin_totals_v1":
        _fail(f"unexpected total-margin model_id {payload.get('model_id')!r}")
    if payload.get("label") != "model_estimate_not_policy":
        _fail("total-margin label changed")
    for field in (
        "observed",
        "policy_grade",
        "combined_national_net_value",
        "ranking_performed",
    ):
        if payload.get(field) is not False:
            _fail(f"total-margin {field} must be false")
    if payload.get("volume_basis") != "119m_allocation":
        _fail("total-margin volume basis must be the 119 Mm3 allocation")
    if payload.get("province_count") != 13:
        _fail("total-margin payload must cover 13 provinces")
    allocation_total = float(payload.get("allocation_total_m3", math.nan))
    if not math.isfinite(allocation_total):
        _fail("total-margin allocation total is missing")

    if len(rows) != 26:
        _fail(f"total-margin ledger has {len(rows)} rows, expected 26")
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        province = _text(row, "province", "total margin")
        scenario = _text(row, "scenario", "total margin")
        label = f"total margin {province}/{scenario}"
        if scenario not in SCENARIO_KEYS:
            _fail(f"{label}: unknown scenario")
        if province not in allocation_by_province:
            _fail(f"{label}: unknown province")
        key = (province, scenario)
        if key in indexed:
            _fail(f"duplicate total-margin row: {key}")
        allocation = _number(row, "allocation_m3", label)
        if abs(allocation - allocation_by_province[province]) > 0.2:
            _fail(f"{label}: allocation does not match 119m.csv")
        margin_low = _number(row, "margin_low_cad_m3", label)
        margin_base = _number(row, "margin_base_cad_m3", label)
        margin_high = _number(row, "margin_high_cad_m3", label)
        total_low = _number(row, "total_low_m_cad", label)
        total_base = _number(row, "total_base_m_cad", label)
        total_high = _number(row, "total_high_m_cad", label)
        if not margin_low <= margin_base <= margin_high:
            _fail(f"{label}: margins are unordered")
        if not total_low <= total_base <= total_high:
            _fail(f"{label}: totals are unordered")
        for case, margin, total in (
            ("low", margin_low, total_low),
            ("base", margin_base, total_base),
            ("high", margin_high, total_high),
        ):
            computed = allocation * margin / 1_000_000.0
            if abs(computed - total) > 1e-4:
                _fail(f"{label}: {case} total does not equal volume x margin")
        if row.get("evidence_tier", "").strip() != "model_estimate":
            _fail(f"{label}: evidence tier must remain model_estimate")
        for field in ("observed", "policy_grade"):
            if row.get(field, "").strip().lower() != "false":
                _fail(f"{label}: {field} must remain false")
        negative_flag = _boolean(row, "negative_flag", label)
        if negative_flag != (margin_base < 0.0):
            _fail(f"{label}: negative flag does not match the base margin")
        if _boolean(row, "zero_volume", label) != (allocation == 0.0):
            _fail(f"{label}: zero-volume flag mismatch")
        indexed[key] = {
            "total_low_m_cad": _round(total_low, 6),
            "total_base_m_cad": _round(total_base, 6),
            "total_high_m_cad": _round(total_high, 6),
            "share_of_national_total_base": _round(
                _number(row, "share_of_national_total_base", label), 10
            ),
            "negative_flag": negative_flag,
            "total_negative_low": _boolean(row, "total_negative_low", label),
            "total_negative_base": _boolean(
                row, "total_negative_base", label
            ),
            "total_negative_high": _boolean(
                row, "total_negative_high", label
            ),
            "zero_volume": allocation == 0.0,
        }
    expected = {
        (province, scenario)
        for province in allocation_by_province
        for scenario in SCENARIO_KEYS
    }
    if set(indexed) != expected:
        _fail("total-margin ledger does not cover 13 provinces x 2 scenarios")

    scenarios = payload.get("scenarios") or {}
    if set(scenarios) != set(SCENARIO_KEYS):
        _fail("total-margin scenarios are incomplete")
    for scenario in SCENARIO_KEYS:
        summary = scenarios[scenario]
        scenario_rows = [
            value for (province, key), value in indexed.items()
            if key == scenario
        ]
        for case in ("low", "base", "high"):
            computed = sum(
                float(value[f"total_{case}_m_cad"])
                for value in scenario_rows
            )
            stated = float(summary[f"national_total_{case}_m_cad"])
            if abs(computed - stated) > 1e-4:
                _fail(f"{scenario}/{case}: national total is not the sum")
        if summary.get("role") != (
            "base" if scenario == SCENARIO_KEYS[0] else "sensitivity"
        ):
            _fail(f"{scenario}: scenario role mismatch")
    if abs(
        sum(allocation_by_province.values()) - allocation_total
    ) > 0.2:
        _fail("total-margin allocation total does not match 119m.csv")
    if allocation_by_province.get("NU") != 0.0:
        _fail("Nunavut must remain zero-volume in the total-margin ledger")

    summary = {
        "model_id": payload.get("model_id"),
        "label": "model_estimate_not_policy",
        "observed": False,
        "policy_grade": False,
        "not_policy_result": True,
        "combined_national_net_value": False,
        "ranking_performed": False,
        "volume_basis": "119m_allocation",
        "allocation_total_m3": _round(allocation_total, 1),
        "province_count": 13,
        "scenarios": {
            scenario: {
                **scenarios[scenario],
                "negative_province_count": int(
                    scenarios[scenario]["negative_province_count"]
                ),
                "positive_province_count": int(
                    scenarios[scenario]["positive_province_count"]
                ),
            }
            for scenario in SCENARIO_KEYS
        },
        "national_diagnostic": payload.get("national_diagnostic") or {},
        "source_csv": _display_path(MARGIN_TOTALS_CSV),
        "source_json": _display_path(MARGIN_TOTALS_JSON),
    }
    return indexed, summary


def _forest_lp_net_value_sensitivity(
    allocation_rows: list[dict[str, str]],
    group_rows: list[dict[str, str]],
    national_rows: list[dict[str, str]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Build the Forest LP net-value sensitivity section from committed rows.

    The section is a reporting join only. It never feeds the Forest LP
    objective, a constraint, or public OSeCAN. Infeasible allocation groups
    keep blank net values and are never back-filled with zero.
    """
    if summary.get("model_id") != NET_VALUE_MODEL_ID:
        _fail(
            "net-value sensitivity model_id mismatch: "
            f"{summary.get('model_id')!r}"
        )
    if summary.get("schema_version") != NET_VALUE_SCHEMA_VERSION:
        _fail("net-value sensitivity schema mismatch")
    if len(allocation_rows) != NET_VALUE_EXPECTED_ALLOCATION_ROWS:
        _fail(
            "net-value sensitivity allocation rows must be "
            f"{NET_VALUE_EXPECTED_ALLOCATION_ROWS}, "
            f"got {len(allocation_rows)}"
        )

    for rows, label in (
        (allocation_rows, "net-value allocation"),
        (group_rows, "net-value allocation summary"),
        (national_rows, "net-value national summary"),
    ):
        _assert_no_unexpected_d(rows, label)

    validation = summary.get("validation") or {}
    if validation.get("allocation_rows") != NET_VALUE_EXPECTED_ALLOCATION_ROWS:
        _fail("net-value summary allocation row count drifted")
    if validation.get("unexpected_d_cells") not in (0, None):
        _fail(
            "net-value sensitivity reports unexpected D cells: "
            f"{validation.get('unexpected_d_cells')!r}"
        )
    statcan_d = summary.get("statcan_d") or {}
    if statcan_d.get("unexpected_cells"):
        _fail(
            "net-value sensitivity reports unexpected StatCan D cells: "
            f"{statcan_d.get('unexpected_cells')!r}"
        )
    if statcan_d.get("observed_d_cell_count") != 0:
        _fail(
            "net-value sensitivity observed D cell count must remain zero: "
            f"{statcan_d.get('observed_d_cell_count')!r}"
        )
    if validation.get("observed_claim") is not False:
        _fail("net-value sensitivity must not claim observed values")
    if validation.get("policy_grade_claim") is not False:
        _fail("net-value sensitivity must not claim policy grade")
    if validation.get("lp_objective") != "not_used":
        _fail("net-value sensitivity lp_objective must be not_used")
    if validation.get("forest_lp_objective_changed") is not False:
        _fail("net-value sensitivity must not change the Forest LP objective")
    if validation.get("public_osecan_input") is not False:
        _fail("net-value sensitivity must not be a public OSeCAN input")

    labels = summary.get("labels") or {}
    expected_labels = {
        "net_value_status": "model_estimate_not_policy",
        "observed": False,
        "policy_grade": False,
        "lp_objective": "not_used",
        "forest_lp_objective_changed": False,
        "v1_outputs_changed": False,
        "combined_national_net_value": False,
        "ranking_performed": False,
        "public_osecan_input": False,
        "restricted_data_used": False,
    }
    for field, expected in expected_labels.items():
        if labels.get(field) != expected:
            _fail(
                f"net-value label {field} must be {expected!r}, "
                f"got {labels.get(field)!r}"
            )

    if len(group_rows) != NET_VALUE_EXPECTED_GROUPS:
        _fail(
            "net-value allocation summary must have "
            f"{NET_VALUE_EXPECTED_GROUPS} groups, got {len(group_rows)}"
        )
    if len(national_rows) != NET_VALUE_EXPECTED_NATIONAL_ROWS:
        _fail(
            "net-value national summary must have "
            f"{NET_VALUE_EXPECTED_NATIONAL_ROWS} rows, "
            f"got {len(national_rows)}"
        )

    national_by_key: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in national_rows:
        key = (
            _text(row, "cap_case", "net-value national"),
            _text(row, "allocation_case", "net-value national"),
            _text(row, "scenario", "net-value national"),
            _text(row, "case", "net-value national"),
        )
        if key in national_by_key:
            _fail(f"net-value national summary duplicate row: {key}")
        national_by_key[key] = row

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(group_rows, start=1):
        label = f"net-value group {index}"
        cap_case = _text(row, "cap_case", label)
        allocation_case = _text(row, "allocation_case", label)
        scenario = _text(row, "scenario", label)
        scenario_role = _text(row, "scenario_role", label)
        case_status = _text(row, "case_status", label)
        if cap_case not in NET_VALUE_CAP_CASES:
            _fail(f"{label}: unexpected cap_case {cap_case!r}")
        if allocation_case not in NET_VALUE_ALLOCATION_CASES:
            _fail(f"{label}: unexpected allocation_case {allocation_case!r}")
        if scenario not in NET_VALUE_SCENARIOS:
            _fail(f"{label}: unexpected scenario {scenario!r}")
        expected_role = "base" if scenario == NET_VALUE_SCENARIOS[0] else "sensitivity"
        if scenario_role != expected_role:
            _fail(
                f"{label}: scenario_role {scenario_role!r} "
                f"must be {expected_role!r}"
            )
        key = (cap_case, allocation_case, scenario)
        if key in seen:
            _fail(f"{label}: duplicate group key {key}")
        seen.add(key)

        feasible = _boolean(row, "case_feasible", label)
        if feasible and case_status != "optimal":
            _fail(f"{label}: feasible group must have status optimal")
        if not feasible and case_status != "infeasible":
            _fail(f"{label}: infeasible group must have status infeasible")

        low = _optional_number(row, "net_value_low_m_cad", label)
        base = _optional_number(row, "net_value_base_m_cad", label)
        high = _optional_number(row, "net_value_high_m_cad", label)
        allocation_m3 = _optional_number(row, "allocation_m3", label)
        count_low = _optional_number(
            row, "negative_net_value_low_count", label
        )
        count_base = _optional_number(
            row, "negative_net_value_base_count", label
        )
        count_high = _optional_number(
            row, "negative_net_value_high_count", label
        )
        if feasible:
            if any(value is None for value in (low, base, high)):
                _fail(f"{label}: feasible group has blank net values")
            assert low is not None and base is not None and high is not None
            if not low <= base <= high:
                _fail(f"{label}: net values are unordered")
            if any(
                value is None
                for value in (count_low, count_base, count_high)
            ):
                _fail(f"{label}: feasible group has blank negative counts")
        else:
            if any(
                value is not None
                for value in (
                    low,
                    base,
                    high,
                    allocation_m3,
                    count_low,
                    count_base,
                    count_high,
                )
            ):
                _fail(
                    f"{label}: infeasible group must keep blank values, "
                    "not zero"
                )

        national_values: dict[str, float] = {}
        for case, expected in (
            ("low", low),
            ("base", base),
            ("high", high),
        ):
            national_row = national_by_key.get(
                (cap_case, allocation_case, scenario, case)
            )
            if national_row is None:
                _fail(f"{label}: missing national {case} row")
            if feasible:
                actual = _number(
                    national_row, "net_value_m_cad", f"{label}/{case}"
                )
                if expected is None or abs(actual - expected) > 1e-6:
                    _fail(
                        f"{label}: national {case} value disagrees with "
                        "the allocation summary"
                    )
                national_values[case] = _round(actual, 6)
            else:
                blank = national_row.get("net_value_m_cad", "").strip()
                if blank:
                    _fail(
                        f"{label}: national {case} row must stay blank "
                        "for an infeasible group"
                    )

        negative_provinces = {
            case: [
                part
                for part in row.get(
                    f"negative_provinces_{case}", ""
                ).split(";")
                if part
            ]
            for case in ("low", "base", "high")
        }
        rows.append(
            {
                "cap_case": cap_case,
                "allocation_case": allocation_case,
                "allocation_label": allocation_case.replace("m", " Mm³"),
                "allocation_target_m3": _round(
                    _number(row, "allocation_target_m3", label), 1
                ),
                "scenario": scenario,
                "scenario_role": scenario_role,
                "scenario_label": scenario_role,
                "case_status": case_status,
                "feasible": feasible,
                "allocation_m3": (
                    None if allocation_m3 is None else _round(allocation_m3, 1)
                ),
                "net_value_low_m_cad": (
                    None if low is None else _round(low, 6)
                ),
                "net_value_base_m_cad": (
                    None if base is None else _round(base, 6)
                ),
                "net_value_high_m_cad": (
                    None if high is None else _round(high, 6)
                ),
                "negative_province_count": (
                    None if count_base is None else int(count_base)
                ),
                "negative_province_counts": {
                    "low": None if count_low is None else int(count_low),
                    "base": None if count_base is None else int(count_base),
                    "high": None if count_high is None else int(count_high),
                },
                "negative_provinces": negative_provinces,
                "observed": False,
                "policy_grade": False,
                "net_value_status": "model_estimate_not_policy",
                "lp_objective": "not_used",
            }
        )

    if seen != {
        (cap_case, allocation_case, scenario)
        for cap_case in NET_VALUE_CAP_CASES
        for allocation_case in NET_VALUE_ALLOCATION_CASES
        for scenario in NET_VALUE_SCENARIOS
    }:
        _fail("net-value groups do not cover every cap x level x scenario")

    documented = [row for row in rows if row["cap_case"] == "documented_ceiling"]
    if len(documented) != 6:
        _fail("net-value documented-ceiling table must have six rows")
    if {
        (row["allocation_case"], row["scenario"]) for row in documented
    } != {
        (allocation_case, scenario)
        for allocation_case in NET_VALUE_ALLOCATION_CASES
        for scenario in NET_VALUE_SCENARIOS
    }:
        _fail("net-value documented-ceiling rows are incomplete")
    for row in documented:
        expected_base = NET_VALUE_EXPECTED_BASE_M_CAD[
            (row["allocation_case"], row["scenario"])
        ]
        if abs(row["net_value_base_m_cad"] - expected_base) > 1e-5:
            _fail(
                "net-value documented-ceiling base total drifted for "
                f"{row['allocation_case']}/{row['scenario']}"
            )

    infeasible = [row for row in rows if not row["feasible"]]
    if len(infeasible) != NET_VALUE_EXPECTED_INFEASIBLE_GROUPS:
        _fail(
            "net-value sensitivity must keep four infeasible intensity rows"
        )
    if {
        (row["cap_case"], row["allocation_case"], row["scenario"])
        for row in infeasible
    } != {
        (cap_case, "150m", scenario)
        for cap_case in ("intensity_1.0", "intensity_1.2")
        for scenario in NET_VALUE_SCENARIOS
    }:
        _fail("net-value infeasible rows must be the 150m intensity screens")

    coverage = dict(summary.get("coverage") or {})
    coverage.update(
        {
            "allocation_rows": len(allocation_rows),
            "expected_allocation_rows": NET_VALUE_EXPECTED_ALLOCATION_ROWS,
            "group_rows": len(group_rows),
            "national_rows": len(national_rows),
            "documented_ceiling_rows": len(documented),
            "infeasible_rows": len(infeasible),
            "cap_cases": list(NET_VALUE_CAP_CASES),
            "allocation_cases": list(NET_VALUE_ALLOCATION_CASES),
            "scenarios": list(NET_VALUE_SCENARIOS),
        }
    )

    return {
        "schema_version": summary.get("schema_version"),
        "model_id": summary.get("model_id"),
        "artifact_version": summary.get("artifact_version"),
        "generated_utc": summary.get("generated_utc"),
        "status": summary.get("status"),
        "source_publication_context": summary.get(
            "source_publication_context"
        ),
        "role": "sensitivity_report_not_lp_objective",
        "display_role": "economics_tab_after_national_totals",
        "labels": {
            "net_value_label": "model_estimate",
            "net_value_status": "model_estimate_not_policy",
            "observed": False,
            "policy_grade": False,
            "lp_objective": "not_used",
            "forest_lp_input": False,
            "public_osecan_input": False,
            "forest_lp_objective_changed": False,
            "v1_outputs_changed": False,
            "combined_national_net_value": False,
            "ranking_performed": False,
            "restricted_data_used": False,
        },
        "coverage": coverage,
        "rows": rows,
        "documented_ceiling_row_count": len(documented),
        "infeasible_row_count": len(infeasible),
        "canonical_119m": summary.get("canonical") or {},
        "validation": validation,
        "statcan_d": statcan_d,
        "caveats": summary.get("caveats") or [],
        "non_claims": summary.get("non_claims") or [],
    }


def _allocation_net_value_lp_sensitivity(
    allocation_rows: list[dict[str, str]],
    delta_rows: list[dict[str, str]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Build the documented-ceiling net-value allocation LP sensitivity.

    The section reports the optimized allocation beside the canonical
    least-deviation baseline. It is derived only from the committed
    per-province CSVs and cross-checked against the committed summary. The
    LP objective is a sensitivity label here; no canonical allocation, UI
    control, Forest LP objective, or public OSeCAN input changes.
    """
    if summary.get("model_id") != ALLOCATION_LP_MODEL_ID:
        _fail(
            "allocation LP net-value model_id mismatch: "
            f"{summary.get('model_id')!r}"
        )
    if summary.get("schema_version") != ALLOCATION_LP_SCHEMA_VERSION:
        _fail("allocation LP net-value schema mismatch")
    if len(allocation_rows) != ALLOCATION_LP_EXPECTED_ALLOCATION_ROWS:
        _fail(
            "allocation LP net-value rows must be "
            f"{ALLOCATION_LP_EXPECTED_ALLOCATION_ROWS}, "
            f"got {len(allocation_rows)}"
        )
    if len(delta_rows) != ALLOCATION_LP_EXPECTED_DELTA_ROWS:
        _fail(
            "allocation LP delta rows must be "
            f"{ALLOCATION_LP_EXPECTED_DELTA_ROWS}, "
            f"got {len(delta_rows)}"
        )
    _assert_no_unexpected_d(allocation_rows, "allocation LP net-value")
    _assert_no_unexpected_d(delta_rows, "allocation LP delta")

    labels = summary.get("labels") or {}
    expected_labels = {
        "evidence": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "lp_objective": "net_value_sensitivity",
        "canonical_changed": False,
        "combined_national_net_value": False,
        "public_osecan_input": False,
        "restricted_data_used": False,
    }
    for field, expected in expected_labels.items():
        if labels.get(field) != expected:
            _fail(
                f"allocation LP label {field} must be {expected!r}, "
                f"got {labels.get(field)!r}"
            )

    validation = summary.get("validation") or {}
    if validation.get("unexpected_d_cells") not in (0, None):
        _fail(
            "allocation LP reports unexpected D cells: "
            f"{validation.get('unexpected_d_cells')!r}"
        )
    if validation.get("observed_d_cell_count") not in (0, None):
        _fail(
            "allocation LP observed D cell count must remain zero: "
            f"{validation.get('observed_d_cell_count')!r}"
        )
    if validation.get("canonical_changed") is not False:
        _fail("allocation LP must not change the canonical allocation")
    if validation.get("canonical_snapshot_unchanged") is not True:
        _fail("allocation LP canonical snapshot check failed")
    if validation.get("baseline_preserved") is not True:
        _fail("allocation LP baseline preservation check failed")
    if validation.get("all_values_model_estimate") is not True:
        _fail("allocation LP must keep every value a model estimate")
    if validation.get("donor_price_caveat_present") is not True:
        _fail("allocation LP must carry the donor-price caveat")

    caveats = summary.get("caveats") or []
    donor_price_caveat = next(
        (
            caveat
            for caveat in caveats
            if "donor/flat price proxy" in caveat
        ),
        None,
    )
    if donor_price_caveat is None:
        _fail("allocation LP donor-price caveat is missing")

    def row_key(row: dict[str, str], label: str) -> tuple[str, str, str, str]:
        return (
            _text(row, "scenario", label),
            _text(row, "cap_case", label),
            _text(row, "level", label),
            _text(row, "province", label),
        )

    allocation_by_key: dict[
        tuple[str, str, str, str], dict[str, str]
    ] = {}
    for index, row in enumerate(allocation_rows, start=1):
        label = f"allocation LP row {index}"
        key = row_key(row, label)
        if key in allocation_by_key:
            _fail(f"allocation LP duplicate row: {key}")
        scenario, cap_case, level, province = key
        if scenario not in ALLOCATION_LP_SCENARIOS:
            _fail(f"{label}: unexpected scenario {scenario!r}")
        if cap_case not in NET_VALUE_CAP_CASES:
            _fail(f"{label}: unexpected cap_case {cap_case!r}")
        if level not in ALLOCATION_LP_LEVELS:
            _fail(f"{label}: unexpected level {level!r}")
        if province not in ALLOCATION_LP_PROVINCES:
            _fail(f"{label}: unexpected province {province!r}")
        expected_role = "base" if scenario == "donor_flat" else "sensitivity"
        if _text(row, "scenario_role", label) != expected_role:
            _fail(f"{label}: scenario_role mismatch")
        _number(row, "baseline_net_value_base_m_cad", label)
        _number(row, "net_value_base_m_cad", label)
        _number(row, "baseline_harvest_m3", label)
        _number(row, "optimized_harvest_m3", label)
        allocation_by_key[key] = row

    delta_by_key: dict[
        tuple[str, str, str, str], dict[str, str]
    ] = {}
    for index, row in enumerate(delta_rows, start=1):
        label = f"allocation LP delta {index}"
        key = row_key(row, label)
        if key in delta_by_key:
            _fail(f"allocation LP duplicate delta row: {key}")
        if key not in allocation_by_key:
            _fail(f"{label}: delta row has no allocation row")
        delta_by_key[key] = row
    if set(delta_by_key) != set(allocation_by_key):
        _fail("allocation LP delta rows do not match the allocation rows")

    all_cases = {
        (scenario, cap_case, level)
        for scenario, cap_case, level, _province in allocation_by_key
    }
    if len(all_cases) != ALLOCATION_LP_EXPECTED_FEASIBLE_CASES:
        _fail(
            "allocation LP feasible cases must be "
            f"{ALLOCATION_LP_EXPECTED_FEASIBLE_CASES}, got {len(all_cases)}"
        )
    for scenario, cap_case, level in all_cases:
        provinces = {
            province
            for key_scenario, key_cap, key_level, province in allocation_by_key
            if (key_scenario, key_cap, key_level)
            == (scenario, cap_case, level)
        }
        if provinces != set(ALLOCATION_LP_PROVINCES):
            _fail(
                "allocation LP province coverage mismatch for "
                f"{scenario}/{cap_case}/{level}"
            )

    summary_totals = summary.get("national_totals") or []
    totals_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in summary_totals:
        key = (
            str(item.get("scenario", "")),
            str(item.get("cap_case", "")),
            str(item.get("level", "")),
        )
        if key in totals_by_key:
            _fail(f"allocation LP summary duplicate case: {key}")
        totals_by_key[key] = item
    if len(totals_by_key) != ALLOCATION_LP_EXPECTED_SUMMARY_CASES:
        _fail("allocation LP summary case count mismatch")

    def summary_number(
        item: dict[str, Any], field: str, label: str
    ) -> float:
        value = item.get(field)
        if not isinstance(value, (int, float)):
            _fail(f"{label}: missing numeric {field}")
        numeric = float(value)
        if not math.isfinite(numeric):
            _fail(f"{label}: non-finite {field}")
        return numeric

    rows: list[dict[str, Any]] = []
    for level in ALLOCATION_LP_LEVELS:
        for scenario in ALLOCATION_LP_SCENARIOS:
            label = f"allocation LP documented {scenario}/{level}"
            provinces = [
                allocation_by_key[(scenario, "documented_ceiling", level, province)]
                for province in ALLOCATION_LP_PROVINCES
            ]
            baseline_m_cad = sum(
                _number(row, "baseline_net_value_base_m_cad", label)
                for row in provinces
            )
            optimized_m_cad = sum(
                _number(row, "net_value_base_m_cad", label)
                for row in provinces
            )
            delta_m_cad = optimized_m_cad - baseline_m_cad
            total = totals_by_key.get(
                (scenario, "documented_ceiling", level)
            )
            if total is None:
                _fail(f"{label}: missing summary total")
            summary_baseline = summary_number(
                total, "baseline_net_value_base_m_cad", label
            )
            summary_optimized = summary_number(
                total, "net_value_base_m_cad", label
            )
            summary_delta = summary_number(
                total, "delta_net_value_base_m_cad", label
            )
            if abs(baseline_m_cad - summary_baseline) > 1e-3:
                _fail(f"{label}: baseline total disagrees with summary")
            if abs(optimized_m_cad - summary_optimized) > 1e-3:
                _fail(f"{label}: optimized total disagrees with summary")
            if abs(delta_m_cad - summary_delta) > 1e-3:
                _fail(f"{label}: delta total disagrees with summary")
            rows.append(
                {
                    "level": level,
                    "level_label": level.replace("m", " Mm³"),
                    "scenario": scenario,
                    "scenario_role": (
                        "base" if scenario == "donor_flat" else "sensitivity"
                    ),
                    "scenario_label": (
                        "base · donor_flat"
                        if scenario == "donor_flat"
                        else "sensitivity · stumpage_scaled"
                    ),
                    "case_status": _text(total, "status", label),
                    "allocation_m3": _round(
                        summary_number(total, "national_total_m3", label), 1
                    ),
                    "baseline_m_cad": _round(baseline_m_cad, 6),
                    "optimized_m_cad": _round(optimized_m_cad, 6),
                    "delta_m_cad": _round(delta_m_cad, 6),
                    "net_value_low_m_cad": _round(
                        summary_number(total, "net_value_low_m_cad", label),
                        6,
                    ),
                    "net_value_high_m_cad": _round(
                        summary_number(total, "net_value_high_m_cad", label),
                        6,
                    ),
                    "evidence": "model_estimate",
                    "observed": False,
                    "policy_grade": False,
                    "lp_objective": "net_value_sensitivity",
                    "canonical_changed": False,
                }
            )
    if len(rows) != ALLOCATION_LP_EXPECTED_DOCUMENTED_ROWS:
        _fail("allocation LP documented table must have six rows")

    province_shifts: list[dict[str, Any]] = []
    for province in ALLOCATION_LP_PROVINCES:
        base_row = allocation_by_key[
            ("donor_flat", "documented_ceiling", ALLOCATION_LP_SHIFT_LEVEL, province)
        ]
        scaled_row = allocation_by_key[
            (
                "stumpage_scaled",
                "documented_ceiling",
                ALLOCATION_LP_SHIFT_LEVEL,
                province,
            )
        ]
        base_baseline_m3 = _number(
            base_row, "baseline_harvest_m3", f"shift {province}"
        )
        scaled_baseline_m3 = _number(
            scaled_row, "baseline_harvest_m3", f"shift {province}"
        )
        if abs(base_baseline_m3 - scaled_baseline_m3) > 2.0:
            _fail(f"shift {province}: baseline allocation differs by scenario")
        baseline_m3 = (base_baseline_m3 + scaled_baseline_m3) / 2.0
        scenarios: dict[str, Any] = {}
        for scenario, allocation_row in (
            ("donor_flat", base_row),
            ("stumpage_scaled", scaled_row),
        ):
            delta_row = delta_by_key[
                (scenario, "documented_ceiling", ALLOCATION_LP_SHIFT_LEVEL, province)
            ]
            optimized_m3 = _number(
                allocation_row,
                "optimized_harvest_m3",
                f"shift {scenario}/{province}",
            )
            delta_m3 = _number(
                delta_row, "delta_m3", f"shift {scenario}/{province}"
            )
            if abs(delta_m3 - (optimized_m3 - baseline_m3)) > 1.0:
                _fail(
                    f"shift {scenario}/{province}: delta volume disagrees"
                )
            scenarios[scenario] = {
                "optimized_m3": _round(optimized_m3, 1),
                "delta_m3": _round(optimized_m3 - baseline_m3, 1),
                "optimized_share": _round(
                    _number(
                        delta_row,
                        "optimized_share",
                        f"shift {scenario}/{province}",
                    ),
                    8,
                ),
                "delta_share_pp": _round(
                    _number(
                        delta_row,
                        "delta_share_pp",
                        f"shift {scenario}/{province}",
                    ),
                    6,
                ),
                "direction": _text(
                    delta_row, "direction", f"shift {scenario}/{province}"
                ),
            }
        province_shifts.append(
            {
                "province": province,
                "province_name": _text(
                    base_row, "province_name", f"shift {province}"
                ),
                "level": ALLOCATION_LP_SHIFT_LEVEL,
                "level_label": ALLOCATION_LP_SHIFT_LEVEL.replace(
                    "m", " Mm³"
                ),
                "baseline_m3": _round(baseline_m3, 1),
                "baseline_share": _round(
                    _number(
                        base_row,
                        "baseline_share",
                        f"shift {province}",
                    ),
                    8,
                ),
                "scenarios": scenarios,
            }
        )
    if len(province_shifts) != ALLOCATION_LP_EXPECTED_PROVINCE_SHIFTS:
        _fail("allocation LP province shift table must have 13 rows")

    return {
        "schema_version": summary.get("schema_version"),
        "model_id": summary.get("model_id"),
        "artifact_version": summary.get("artifact_version"),
        "generated_utc": summary.get("generated_utc"),
        "status": summary.get("status"),
        "role": "report_only_not_canonical_allocation",
        "labels": {
            "evidence": "model_estimate",
            "net_value_label": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "lp_objective": "net_value_sensitivity",
            "canonical_changed": False,
            "baseline_objective": labels.get("baseline_objective"),
            "combined_national_net_value": False,
            "public_osecan_input": False,
            "restricted_data_used": False,
            "ranking_performed": False,
            "forest_lp_input": False,
        },
        "donor_price_caveat": donor_price_caveat,
        "coverage": {
            "allocation_rows": len(allocation_rows),
            "delta_rows": len(delta_rows),
            "feasible_case_count": len(all_cases),
            "summary_case_count": len(totals_by_key),
            "documented_ceiling_rows": len(rows),
            "province_shift_rows": len(province_shifts),
            "province_shift_level": ALLOCATION_LP_SHIFT_LEVEL,
            "province_count": len(ALLOCATION_LP_PROVINCES),
            "levels": list(ALLOCATION_LP_LEVELS),
            "scenarios": list(ALLOCATION_LP_SCENARIOS),
        },
        "rows": rows,
        "province_shifts": province_shifts,
        "caveats": caveats,
        "non_claims": summary.get("non_claims") or [],
        "source_csv": _display_path(ALLOCATION_LP_CSV),
        "source_delta_csv": _display_path(ALLOCATION_LP_DELTA_CSV),
        "source_json": _display_path(ALLOCATION_LP_SUMMARY_JSON),
    }


def build_export(
    source_dir: Path = SOURCE_DIR,
    tier_source: Path = TIER_SOURCE,
    *,
    check_deletions: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the national UI payload and manifest from committed CSVs."""
    if check_deletions:
        assert_no_unexpected_deletions(REPO_ROOT)

    summary = _read_json(source_dir / SUMMARY_JSON)
    if summary.get("model_id") != MODEL_ID:
        _fail(f"unexpected model_id {summary.get('model_id')!r}")
    margin_rows = _read_csv(source_dir / MARGIN_CSV)
    summary_rows = _read_csv(source_dir / SUMMARY_CSV)
    comparison_rows = _read_csv(source_dir / COMPARISON_CSV)
    tier_rows = _read_csv(source_dir / TIER_CSV)
    cost_rows = _read_csv(source_dir / COST_INPUTS_CSV)
    price_cell_rows = _read_csv(tier_source)
    price_source_rows = _read_csv(PRICE_SOURCES)
    stumpage_rows = _read_csv(STUMPAGE_SOURCE)
    allocation_rows = _read_csv(ALLOCATION_SOURCE)
    total_margin_rows = _read_csv(MARGIN_TOTALS_CSV)
    total_margin_payload = _read_json(MARGIN_TOTALS_JSON)
    tier_summary = _read_json(TIER_SUMMARY_JSON)
    statcan_rows = _read_csv(STATCAN_DIR / STATCAN_CSV)
    statcan_source_rows = _read_csv(STATCAN_DIR / STATCAN_SOURCES_CSV)
    net_value_allocation_rows = _read_csv(NET_VALUE_ALLOCATION_CSV)
    net_value_group_rows = _read_csv(NET_VALUE_GROUP_CSV)
    net_value_national_rows = _read_csv(NET_VALUE_NATIONAL_CSV)
    net_value_summary = _read_json(NET_VALUE_SUMMARY_JSON)
    allocation_lp_rows = _read_csv(ALLOCATION_LP_CSV)
    allocation_lp_delta_rows = _read_csv(ALLOCATION_LP_DELTA_CSV)
    allocation_lp_summary = _read_json(ALLOCATION_LP_SUMMARY_JSON)

    coverage = summary.get("coverage") or {}
    provinces = list(coverage.get("provinces") or [])
    if len(provinces) != 13 or len(set(provinces)) != 13:
        _fail("summary coverage does not list 13 unique jurisdictions")
    if coverage.get("missing") != [] or coverage.get("not_estimated_count") != 0:
        _fail("summary coverage reports missing or not-estimated jurisdictions")
    if (
        coverage.get("margin_rows") != 78
        or coverage.get("summary_rows") != 30
        or coverage.get("comparison_rows") != 30
        or coverage.get("cost_input_rows") != 18
        or coverage.get("tier_rows") != 42
    ):
        _fail("summary coverage row counts changed")

    decision = summary.get("decision") or {}
    base_scenario = decision.get("base_scenario")
    sensitivity_scenario = decision.get("sensitivity_scenario")
    if (base_scenario, sensitivity_scenario) != SCENARIO_KEYS:
        _fail("expected donor_flat as base and stumpage_scaled as sensitivity")
    for key in ("stumpage_scaled_observed", "stumpage_scaled_policy_grade"):
        if decision.get(key) is not False:
            _fail(f"decision.{key} must be false")

    for rows, label in (
        (margin_rows, "margin estimates"),
        (summary_rows, "margin summary"),
        (comparison_rows, "v2.2/v2.3 comparison"),
        (tier_rows, "tier coverage"),
        (cost_rows, "cost inputs"),
        (price_cell_rows, "price cell audit"),
        (stumpage_rows, "stumpage references"),
        (allocation_rows, "provincial allocation"),
        (total_margin_rows, "total margin"),
    ):
        _validate_labels(rows, label)
    for row in statcan_rows:
        if row.get("observed", "").strip() not in {"", "false"}:
            _fail("StatCan proxy must not claim observed values")
        if row.get("policy_grade", "").strip() not in {"", "false"}:
            _fail("StatCan proxy must not claim policy grade")

    statcan_d = _validate_statcan_d(statcan_rows, summary)
    if statcan_d["observed_d_cell_count"] == 0:
        _fail("expected the known StatCan D quality flags")

    estimates = {
        (row["price_scenario"], row["province"], row["scenario_id"]): row
        for row in margin_rows
    }
    if len(estimates) != 78:
        _fail(f"margin estimates have {len(estimates)} rows, expected 78")
    summary_by_key = {
        (row["price_scenario"], row["province"]): row
        for row in summary_rows
        if row.get("row_type") == "province"
    }
    if len(summary_by_key) != 26:
        _fail("margin summary must have 26 province rows")
    cost_by_province, _cost_meta = _cost_inputs(cost_rows, summary_rows)
    comparison_by_key = {
        (row["price_scenario"], row["province"]): row
        for row in comparison_rows
        if row.get("row_type") == "province"
    }
    if len(comparison_by_key) != 26:
        _fail("v2.2/v2.3 comparison must have 26 province rows")

    price_sources = _source_map(PRICE_SOURCES)
    price_cells_by_province, _ = _tier_cells(price_cell_rows, price_sources)
    stumpage_by_province, stumpage_meta = _stumpage_references(
        STUMPAGE_SOURCE
    )
    allocation_by_province: dict[str, float] = {}
    for row in allocation_rows:
        province = _text(row, "province", "provincial allocation")
        allocation_by_province[province] = _number(
            row, "harvest_m3", f"provincial allocation {province}"
        )
    if set(allocation_by_province) != set(provinces):
        _fail("provincial allocation does not match the 13 summary provinces")
    total_margins_by_key, total_margin_summary = _margin_totals(
        total_margin_rows, total_margin_payload, allocation_by_province
    )
    net_value_sensitivity = _forest_lp_net_value_sensitivity(
        net_value_allocation_rows,
        net_value_group_rows,
        net_value_national_rows,
        net_value_summary,
    )
    allocation_lp_sensitivity = _allocation_net_value_lp_sensitivity(
        allocation_lp_rows,
        allocation_lp_delta_rows,
        allocation_lp_summary,
    )
    net_value_sensitivity["economic_allocation_sensitivity"] = (
        allocation_lp_sensitivity
    )

    jurisdictions: list[dict[str, Any]] = []
    for province in provinces:
        scenario_values: dict[str, Any] = {}
        first_row: dict[str, str] | None = None
        for scenario in SCENARIO_KEYS:
            row = summary_by_key.get((scenario, province))
            if row is None:
                _fail(f"missing {scenario} summary row for {province}")
            case_rows = {
                case: estimates.get((scenario, province, case))
                for case in ("low", "base", "high")
            }
            if any(item is None for item in case_rows.values()):
                _fail(f"missing {scenario} estimate rows for {province}")
            low = _number(row, "margin_low_cad_m3", province)
            base = _number(row, "margin_base_cad_m3", province)
            high = _number(row, "margin_high_cad_m3", province)
            if not low <= base <= high:
                _fail(f"{scenario}/{province}: unordered margins")
            for case, expected in (("low", low), ("base", base), ("high", high)):
                actual = _number(
                    case_rows[case], "margin_cad_m3", f"{scenario}/{province}"
                )
                if abs(actual - expected) > 1e-6:
                    _fail(f"{scenario}/{province}: summary and estimate disagree")
            scenario_values[scenario] = {
                "low_cad_m3": _round(low, 4),
                "base_cad_m3": _round(base, 4),
                "high_cad_m3": _round(high, 4),
                "price_tier": _text(row, "price_tier", province),
                "price_basis": _text(row, "price_basis", province),
                "cost_tier": COST_TIER_MAP[
                    _text(row, "cost_tier", province)
                ],
                "cost_tier_source": _text(row, "cost_tier", province),
            }
            if first_row is None:
                first_row = row
        if first_row is None:
            _fail(f"no rows for {province}")

        comparison = comparison_by_key.get((base_scenario, province))
        if comparison is None:
            _fail(f"missing v2.2/v2.3 comparison for {province}")
        v23_base = _number(
            comparison, "v2_3_margin_base_cad_m3", province
        )
        if abs(v23_base - scenario_values[base_scenario]["base_cad_m3"]) > 1e-6:
            _fail(f"comparison/{province}: v2.3 base margin mismatch")
        v22_base = _number(
            comparison, "v2_2_margin_base_cad_m3", province
        )
        margin_delta = _number(
            comparison, "margin_base_delta_cad_m3", province
        )
        if abs(margin_delta - (v23_base - v22_base)) > 1e-6:
            _fail(f"comparison/{province}: margin delta mismatch")
        if comparison.get("comparison_status") != (
            "cost_method_change_not_observed"
        ):
            _fail(f"comparison/{province}: unexpected status")

        cost = cost_by_province[province]
        price_cells = price_cells_by_province.get(province) or []
        if len(price_cells) != 5:
            _fail(f"price cells are incomplete for {province}")
        price_cell = next(
            cell for cell in price_cells if cell["component"] == "price"
        )
        price_source = price_sources.get(price_cell["source_key"]) or {}
        mix = Counter(cell["tier"] for cell in price_cells)
        direct_cell_count = mix.get("reported_average", 0)
        proxy_cell_count = mix.get("proxy_model", 0)
        donor_cell_count = mix.get("donor_proxy", 0)
        donor_only = direct_cell_count == 0 and proxy_cell_count == 0
        if abs(
            allocation_by_province[province]
            - _number(first_row, "allocation_volume_m3", province)
        ) > 0.2:
            _fail(f"allocation volume mismatch for {province}")

        status, status_label = _jurisdiction_status(
            cost["tier"], province, donor_only
        )
        jurisdictions.append(
            {
                "province": province,
                "province_name": first_row["province_name"],
                "observed_2024_volume_m3": _round(
                    _number(first_row, "observed_2024_volume_m3", province), 1
                ),
                "allocation_volume_m3": _round(
                    allocation_by_province[province], 1
                ),
                "volume_share_observed": _round(
                    _number(first_row, "volume_share_observed", province), 8
                ),
                "volume_share_119m": _round(
                    _number(first_row, "volume_share_119m", province), 8
                ),
                "price_tier": scenario_values[base_scenario]["price_tier"],
                "price_basis": scenario_values[base_scenario]["price_basis"],
                "price_source": {
                    "source_key": price_cell["source_key"],
                    "title": price_source.get("title")
                    or price_cell["source_title"],
                    "publication_date": price_source.get("publication_date")
                    or "",
                    "source_url": price_source.get("url") or "",
                    "is_direct": price_cell["tier"] == "reported_average",
                    "donor_province": price_cell["donor_province"],
                },
                "cost_tier": cost["tier"],
                "cost_tier_source": cost["tier_source"],
                "cost_tier_label": cost["label"],
                "cost_source": cost["source"],
                "cost_components": cost["components"],
                "total_cost_low_cad_m3": cost["low_cad_m3"],
                "total_cost_base_cad_m3": cost["base_cad_m3"],
                "total_cost_high_cad_m3": cost["high_cad_m3"],
                "donor_flat": scenario_values[base_scenario],
                "stumpage_scaled": scenario_values[sensitivity_scenario],
                "scaled_minus_donor_flat_cad_m3": _round(
                    scenario_values[sensitivity_scenario]["base_cad_m3"]
                    - scenario_values[base_scenario]["base_cad_m3"],
                    4,
                ),
                "v2_2_to_v2_3": {
                    "v2_2_margin_base_cad_m3": _round(v22_base, 4),
                    "v2_3_margin_base_cad_m3": _round(v23_base, 4),
                    "margin_base_delta_cad_m3": _round(margin_delta, 4),
                    "total_cost_base_delta_cad_m3": _round(
                        _number(
                            comparison,
                            "total_cost_base_delta_cad_m3",
                            province,
                        ),
                        4,
                    ),
                    "status": "cost_method_change_not_observed",
                },
                "direct_cell_count": direct_cell_count,
                "proxy_cell_count": proxy_cell_count,
                "donor_cell_count": donor_cell_count,
                "donor_only": donor_only,
                "status": status,
                "status_label": status_label,
                "is_direct": price_cell["tier"] == "reported_average",
                "ranked": False,
                "total_margin_119m": {
                    "volume_basis": "119m_allocation",
                    "allocation_m3": _round(
                        allocation_by_province[province], 1
                    ),
                    "donor_flat": total_margins_by_key[
                        (province, SCENARIO_KEYS[0])
                    ],
                    "stumpage_scaled": total_margins_by_key[
                        (province, SCENARIO_KEYS[1])
                    ],
                },
                "stumpage_reference": stumpage_by_province[province],
                "price_evidence": price_cells,
            }
        )

    if len(jurisdictions) != 13:
        _fail("jurisdiction table is incomplete")

    direct_jurisdictions = [
        row["province"] for row in jurisdictions if row["price_tier"] == "reported_average"
    ]
    donor_price_jurisdictions = [
        row["province"] for row in jurisdictions if row["price_tier"] == "donor_proxy"
    ]
    donor_only_jurisdictions = [
        row["province"] for row in jurisdictions if row["donor_only"]
    ]
    aggregate_jurisdictions = [
        row["province"]
        for row in jurisdictions
        if row["cost_tier"] == COST_TIER_AGGREGATE
    ]
    component_jurisdictions = [
        row["province"]
        for row in jurisdictions
        if row["cost_tier"] == COST_TIER_COMPONENT
    ]
    fallback_jurisdictions = [
        row["province"]
        for row in jurisdictions
        if row["cost_tier"] == COST_TIER_FALLBACK
    ]
    if direct_jurisdictions != ["BC"]:
        _fail(f"expected BC as the only direct/reported price jurisdiction: {direct_jurisdictions}")
    if len(donor_price_jurisdictions) != 12:
        _fail("expected twelve donor-price jurisdictions")
    if len(donor_only_jurisdictions) != 11:
        _fail("expected eleven donor-only price jurisdictions")
    if len(aggregate_jurisdictions) != 10:
        _fail("expected ten aggregate-proxy jurisdictions")
    if component_jurisdictions != ["BC", "QC"]:
        _fail(f"expected BC and QC component stacks: {component_jurisdictions}")
    if fallback_jurisdictions != ["NU"]:
        _fail(f"expected NU as the only donor fallback: {fallback_jurisdictions}")

    cost_coverage = _cost_coverage(tier_rows)
    after_observed = cost_coverage["volume_bases"][VOLUME_BASIS_OBSERVED]
    price_audit = _tier_coverage_audit(tier_source)
    tier_summary_shares = (tier_summary.get("volume_weighted") or {}).get(
        "tier_share"
    ) or {}
    for tier, share in price_audit["volume_weighted_shares"].items():
        source_share = tier_summary_shares.get(tier)
        if source_share is None or abs(float(source_share) - share) > 1e-8:
            _fail(f"price tier coverage source diverges for {tier}")

    # Price direct coverage is province-level under the 119 Mm3 allocation
    # basis, matching the prior live panel. Cost coverage uses the v2.3
    # observed-2024 primary denominator.
    price_direct_share = sum(
        row["volume_share_119m"]
        for row in jurisdictions
        if row["price_tier"] == "reported_average"
    )
    price_donor_share = 1.0 - price_direct_share
    if abs(price_direct_share - 0.33601842) > 5e-9:
        _fail(f"unexpected direct/reported price share: {price_direct_share}")

    cost_shares = after_observed["shares"]
    evidence_coverage = {
        "default_view": "evidence_coverage",
        "basis": (
            "13 jurisdictions; 65 five-component price/evidence cells; "
            "one province-level cost-basis cell per jurisdiction"
        ),
        "jurisdiction_count": len(jurisdictions),
        "cell_count": price_audit["cell_count"],
        "component_count": len(COMPONENTS),
        "volume_weighted": {
            "direct_reported": {
                "share": _round(price_direct_share, 8),
                "pct": _round(price_direct_share * 100, 4),
                "cells": price_audit["cell_counts"]["reported_average"],
                "jurisdictions": direct_jurisdictions,
                "label": "direct / reported price",
                "volume_basis": VOLUME_BASIS_119M,
            },
            "donor": {
                "share": _round(price_donor_share, 8),
                "pct": _round(price_donor_share * 100, 4),
                "cells": price_audit["cell_counts"]["donor_proxy"],
                "jurisdictions": donor_price_jurisdictions,
                "label": "donor price",
                "volume_basis": VOLUME_BASIS_119M,
            },
            "assumption": {
                "share": price_audit["volume_weighted_shares"]["assumption"],
                "pct": price_audit["volume_weighted_pct"]["assumption"],
                "cells": price_audit["cell_counts"]["assumption"],
                "label": "assumption",
                "volume_basis": VOLUME_BASIS_119M,
            },
        },
        "price_evidence": {
            "cell_count": price_audit["cell_count"],
            "component_count": len(COMPONENTS),
            "cell_mix": price_audit["volume_weighted_shares"],
            "cell_mix_pct": price_audit["volume_weighted_pct"],
            "cell_counts": price_audit["cell_counts"],
            "province_volume_basis": VOLUME_BASIS_119M,
        },
        "cost_evidence": {
            "basis": "province_level_cost_basis_cells",
            "volume_basis": VOLUME_BASIS_OBSERVED,
            "sensitivity_volume_basis": VOLUME_BASIS_119M,
            "volume_weighted": {
                COST_TIER_COMPONENT: {
                    "share": cost_shares[COST_TIER_COMPONENT],
                    "pct": after_observed["pct"][COST_TIER_COMPONENT],
                    "cells": after_observed["component_cells"],
                    "jurisdictions": component_jurisdictions,
                    "label": COST_TIER_LABEL[COST_TIER_COMPONENT],
                },
                COST_TIER_AGGREGATE: {
                    "share": cost_shares[COST_TIER_AGGREGATE],
                    "pct": after_observed["pct"][COST_TIER_AGGREGATE],
                    "cells": after_observed["aggregate_proxy_cells"],
                    "jurisdictions": aggregate_jurisdictions,
                    "label": COST_TIER_LABEL[COST_TIER_AGGREGATE],
                },
                COST_TIER_FALLBACK: {
                    "share": cost_shares[COST_TIER_FALLBACK],
                    "pct": after_observed["pct"][COST_TIER_FALLBACK],
                    "cells": after_observed["donor_fallback_cells"],
                    "jurisdictions": fallback_jurisdictions,
                    "label": COST_TIER_LABEL[COST_TIER_FALLBACK],
                },
            },
            "sensitivity_119m": cost_coverage["volume_bases"][VOLUME_BASIS_119M],
            "before_v2_2": cost_coverage["before"][VOLUME_BASIS_OBSERVED],
            "upgrade_count": cost_coverage["upgrade_count"],
            "full_direct_coverage_claimed": False,
        },
        "industry_aggregate_proxy": {
            "share": cost_shares[COST_TIER_AGGREGATE],
            "pct": after_observed["pct"][COST_TIER_AGGREGATE],
            "cells": after_observed["aggregate_proxy_cells"],
            "jurisdictions": aggregate_jurisdictions,
            "label": COST_TIER_LABEL[COST_TIER_AGGREGATE],
        },
        "component_stack": {
            "share": cost_shares[COST_TIER_COMPONENT],
            "pct": after_observed["pct"][COST_TIER_COMPONENT],
            "cells": after_observed["component_cells"],
            "jurisdictions": component_jurisdictions,
            "label": COST_TIER_LABEL[COST_TIER_COMPONENT],
        },
        "donor_fallback": {
            "share": cost_shares[COST_TIER_FALLBACK],
            "pct": after_observed["pct"][COST_TIER_FALLBACK],
            "cells": after_observed["donor_fallback_cells"],
            "jurisdictions": fallback_jurisdictions,
            "label": COST_TIER_LABEL[COST_TIER_FALLBACK],
        },
        "direct_jurisdictions": direct_jurisdictions,
        "donor_only_jurisdictions": donor_only_jurisdictions,
        "donor_price_jurisdictions": donor_price_jurisdictions,
        "aggregate_proxy_jurisdictions": aggregate_jurisdictions,
        "component_stack_jurisdictions": component_jurisdictions,
        "donor_fallback_jurisdictions": fallback_jurisdictions,
        "direct_cell_count": price_audit["cell_counts"]["reported_average"],
        "proxy_cell_count": price_audit["cell_counts"]["proxy_model"],
        "donor_cell_count": price_audit["cell_counts"]["donor_proxy"],
        "assumption_cell_count": price_audit["cell_counts"]["assumption"],
        "direct_jurisdiction_count": len(direct_jurisdictions),
        "donor_only_count": len(donor_only_jurisdictions),
        "donor_price_count": len(donor_price_jurisdictions),
        "aggregate_proxy_count": len(aggregate_jurisdictions),
        "donor_fallback_count": len(fallback_jurisdictions),
        "not_decision_grade": True,
        "warning": (
            f"{price_direct_share * 100:.1f}% of volume-weighted price evidence "
            f"is direct/reported (BC); {cost_shares[COST_TIER_AGGREGATE] * 100:.1f}% "
            "of cost evidence is a provisional industry aggregate proxy. "
            f"{len(aggregate_jurisdictions)} jurisdictions are provisional "
            f"aggregate, not direct; {fallback_jurisdictions[0]} is the only "
            "donor-fallback cost. These margins are not decision-grade and are "
            "not ranked."
        ),
    }

    national_payload = _national_diagnostic(summary_rows, summary)
    national_delta = _round(
        national_payload["stumpage_scaled"]["base_cad_m3"]
        - national_payload["donor_flat"]["base_cad_m3"],
        4,
    )
    national_comparison_row = next(
        (
            row
            for row in comparison_rows
            if row.get("row_type") == "national"
            and row.get("volume_basis") == VOLUME_BASIS_OBSERVED
            and row.get("price_scenario") == base_scenario
        ),
        None,
    )
    if national_comparison_row is None:
        _fail("v2.2/v2.3 comparison is missing the national observed row")

    labels = summary.get("labels") or {}
    if labels.get("evidence") != "model_estimate":
        _fail("summary evidence label changed")
    if labels.get("observed") is not False or labels.get("policy_grade") is not False:
        _fail("summary labels must remain unobserved and not policy grade")
    if labels.get("combined_national_net_value") is not False:
        _fail("summary must not claim a combined national net value")

    source_files = [
        (source_dir / MARGIN_CSV, len(margin_rows)),
        (source_dir / SUMMARY_CSV, len(summary_rows)),
        (source_dir / COMPARISON_CSV, len(comparison_rows)),
        (source_dir / TIER_CSV, len(tier_rows)),
        (source_dir / COST_INPUTS_CSV, len(cost_rows)),
        (source_dir / SUMMARY_JSON, None),
        (tier_source, price_audit["cell_count"]),
        (TIER_SUMMARY_JSON, None),
        (PRICE_SOURCES, len(price_source_rows)),
        (STUMPAGE_SOURCE, len(stumpage_rows)),
        (ALLOCATION_SOURCE, len(allocation_rows)),
        (MARGIN_TOTALS_CSV, len(total_margin_rows)),
        (MARGIN_TOTALS_JSON, None),
        (NET_VALUE_ALLOCATION_CSV, len(net_value_allocation_rows)),
        (NET_VALUE_GROUP_CSV, len(net_value_group_rows)),
        (NET_VALUE_NATIONAL_CSV, len(net_value_national_rows)),
        (NET_VALUE_SUMMARY_JSON, None),
        (ALLOCATION_LP_CSV, len(allocation_lp_rows)),
        (ALLOCATION_LP_DELTA_CSV, len(allocation_lp_delta_rows)),
        (ALLOCATION_LP_SUMMARY_JSON, None),
        (STATCAN_DIR / STATCAN_CSV, len(statcan_rows)),
        (STATCAN_DIR / STATCAN_SOURCES_CSV, len(statcan_source_rows)),
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
            "forest_lp_net_value_sensitivity",
            "economic_allocation_sensitivity",
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
            "national_total_margin_label": "model_estimate_not_policy",
            "total_margin_policy_result": False,
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
            "price_cell_count": price_audit["cell_count"],
            "cost_cell_count": cost_coverage["cell_count"],
        },
        "evidence_coverage": evidence_coverage,
        "ranking": {
            "performed": False,
            "ordering": "canonical_jurisdiction_order",
            "aggregate_proxy_excluded": True,
            "reason": (
                "Aggregate-proxy and donor-fallback jurisdictions are "
                "provisional evidence rows. The UI does not sort or rank "
                "margins across jurisdictions."
            ),
        },
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
        "national_diagnostic": {
            **national_payload,
            "national_comparison": {
                "scaled_minus_donor_flat_cad_m3": national_delta,
                "dues_comparable_to_delivered_price": False,
                "comparison_status": "cost_method_change_not_observed",
                "volume_basis": VOLUME_BASIS_OBSERVED,
                "v2_2_margin_base_cad_m3": _round(
                    _number(
                        national_comparison_row,
                        "v2_2_margin_base_cad_m3",
                        "national comparison",
                    ),
                    4,
                ),
            },
        },
        "national_margin_totals": total_margin_summary,
        "forest_lp_net_value_sensitivity": net_value_sensitivity,
        "scenarios": [
            {
                "id": scenario,
                "role": "base" if scenario == base_scenario else "sensitivity",
                "label": (
                    "Base · donor_flat"
                    if scenario == base_scenario
                    else "Sensitivity · stumpage_scaled"
                ),
                "is_base": scenario == base_scenario,
                "price_tier_counts": dict(
                    Counter(
                        row["price_tier"]
                        for row in summary_rows
                        if row.get("row_type") == "province"
                        and row.get("price_scenario") == scenario
                    )
                ),
            }
            for scenario in SCENARIO_KEYS
        ],
        "jurisdictions": jurisdictions,
        "tier_coverage": {
            **price_audit,
            "cost": cost_coverage,
        },
        "statcan_d_status": statcan_d,
        "caveats": [
            (
                "Evidence coverage comes first. Price evidence and cost "
                "evidence are separate overlays: 65 five-component price cells "
                "and 13 province-level cost-basis cells."
            ),
            (
                "Price direct/reported coverage is 33.6% of the 119 Mm3 "
                "allocation; the remaining 66.4% uses a donor price."
            ),
            (
                "Ten jurisdictions use a provisional StatCan NAICS 11331 "
                "industry aggregate cost proxy, 45.5% of observed 2024 harvest. "
                "It is not direct coverage."
            ),
            AGGREGATE_CAVEAT,
            (
                "Nunavut is the only donor-fallback cost jurisdiction; StatCan "
                "reports zero expenses and zero harvest, so no CAD/m3 ratio is "
                "fabricated."
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
                "standing timber, not delivered log prices."
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
            "The StatCan aggregate is not a logging or haul cost curve.",
        ],
        "source_files": [
            {"path": _display_path(path), "rows": rows}
            for path, rows in source_files
        ],
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
            for item in payload["source_files"]
        ],
        "validation": {
            "ok": True,
            "default_view": payload["default_view"],
            "jurisdiction_count": len(jurisdictions),
            "price_cell_count": price_audit["cell_count"],
            "direct_reported_price_share": _round(price_direct_share, 8),
            "industry_aggregate_proxy_share": cost_shares[
                COST_TIER_AGGREGATE
            ],
            "component_stack_share": cost_shares[COST_TIER_COMPONENT],
            "donor_fallback_share": cost_shares[COST_TIER_FALLBACK],
            "direct_cell_count": price_audit["cell_counts"]["reported_average"],
            "proxy_cell_count": price_audit["cell_counts"]["proxy_model"],
            "donor_cell_count": price_audit["cell_counts"]["donor_proxy"],
            "aggregate_proxy_count": len(aggregate_jurisdictions),
            "donor_fallback_count": len(fallback_jurisdictions),
            "direct_jurisdiction_count": len(direct_jurisdictions),
            "donor_only_count": len(donor_only_jurisdictions),
            "donor_price_count": len(donor_price_jurisdictions),
            "stumpage_rate_count": stumpage_meta["rate_count"],
            "stumpage_fallback_count": stumpage_meta["fallback_count"],
            "ranking_performed": False,
            "not_decision_grade": True,
            "base_national_margin_cad_m3": national_payload["donor_flat"][
                "base_cad_m3"
            ],
            "sensitivity_national_margin_cad_m3": national_payload[
                "stumpage_scaled"
            ]["base_cad_m3"],
            "national_total_margin_donor_flat_m_cad": total_margin_summary[
                "scenarios"
            ]["donor_flat"]["national_total_base_m_cad"],
            "national_total_margin_stumpage_scaled_m_cad": total_margin_summary[
                "scenarios"
            ]["stumpage_scaled"]["national_total_base_m_cad"],
            "national_total_margin_province_count": total_margin_summary[
                "province_count"
            ],
            "national_total_margin_allocation_m3": total_margin_summary[
                "allocation_total_m3"
            ],
            "national_total_margin_label": total_margin_summary["label"],
            "national_total_margin_policy_result": False,
            "net_value_sensitivity_model_id": net_value_sensitivity["model_id"],
            "net_value_sensitivity_allocation_rows": net_value_sensitivity[
                "coverage"
            ]["allocation_rows"],
            "net_value_sensitivity_groups": net_value_sensitivity["coverage"][
                "group_rows"
            ],
            "net_value_sensitivity_documented_ceiling_rows": net_value_sensitivity[
                "documented_ceiling_row_count"
            ],
            "net_value_sensitivity_infeasible_rows": net_value_sensitivity[
                "infeasible_row_count"
            ],
            "net_value_sensitivity_label": net_value_sensitivity["labels"][
                "net_value_status"
            ],
            "net_value_sensitivity_lp_objective": net_value_sensitivity[
                "labels"
            ]["lp_objective"],
            "net_value_sensitivity_observed_claim": False,
            "net_value_sensitivity_policy_grade_claim": False,
            "net_value_sensitivity_unexpected_d_cells": net_value_sensitivity[
                "statcan_d"
            ].get("observed_d_cell_count", 0),
            "allocation_net_value_lp_model_id": allocation_lp_sensitivity[
                "model_id"
            ],
            "allocation_net_value_lp_documented_rows": allocation_lp_sensitivity[
                "coverage"
            ]["documented_ceiling_rows"],
            "allocation_net_value_lp_province_shift_rows": allocation_lp_sensitivity[
                "coverage"
            ]["province_shift_rows"],
            "allocation_net_value_lp_lp_objective": allocation_lp_sensitivity[
                "labels"
            ]["lp_objective"],
            "allocation_net_value_lp_canonical_changed": allocation_lp_sensitivity[
                "labels"
            ]["canonical_changed"],
            "allocation_net_value_lp_unexpected_d_cells": 0,
            "observed_claim": False,
            "policy_grade_claim": False,
            "combined_national_net_value": False,
            "forest_lp_input": False,
            "public_osecan_input": False,
            "deletions_checked": check_deletions,
            "statcan_unexpected_d_cells": 0,
        },
    }
    return payload, manifest


def _jurisdiction_status(
    cost_tier: str, province: str, donor_only: bool
) -> tuple[str, str]:
    if cost_tier == COST_TIER_AGGREGATE:
        return "provisional_aggregate", "provisional aggregate"
    if cost_tier == COST_TIER_FALLBACK:
        return "provisional_donor_fallback", "provisional donor fallback"
    if province == "QC":
        return "component_stack_survey", "component stack · survey"
    if donor_only:
        return "provisional_donor", "provisional donor"
    return "partial_direct", "partial direct"


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
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
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
        description="Export national economics v2.3 for the live UI."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=SOURCE_DIR,
        help="national_economics_v2_3 directory",
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
    coverage = payload["evidence_coverage"]
    print(
        "national economics v2.3 export: "
        f"{payload['coverage']['jurisdiction_rows']} jurisdictions, "
        f"price_direct={coverage['volume_weighted']['direct_reported']['pct']:.4f}% "
        f"aggregate_proxy={coverage['industry_aggregate_proxy']['pct']:.4f}% "
        f"donor_fallback={coverage['donor_fallback']['pct']:.4f}%, "
        f"sha256={manifest['generated'][0]['sha256'][:16]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
