"""Export national economics v2.1 into the read-only live UI bundle.

The export is derived only from committed CSVs and ``summary.json``.  It never
invents a national total: the two national rows are volume-weighted diagnostic
values and stay ``model_estimate_not_policy``.

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
OUT_JSON = REPO_ROOT / "ui/public/data/economics/national.json"
OUT_MANIFEST = REPO_ROOT / "ui/public/data/economics/national.manifest.json"

SCHEMA_VERSION = "economics/national/1"
MANIFEST_SCHEMA_VERSION = "economics/national-manifest/1"
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
    if diagnostic.get("allocation_volume_m3") != 118_977_339.1:
        _fail("national allocation volume changed")

    national_payload: dict[str, Any] = {
        "allocation_volume_m3": _round(
            _number(national_rows[0], "allocation_volume_m3", "national"), 1
        ),
        "label": diagnostic["label"],
        "combined_national_net_value": False,
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
    ]

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_id": summary["model_id"],
        "artifact_version": summary.get("artifact_version"),
        "generated_utc": summary.get("generated_utc"),
        "status": summary.get("status"),
        "role": "national_model_estimate_diagnostic",
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
                "National values are volume-weighted model diagnostics, not a "
                "combined national net value."
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
            "jurisdiction_count": len(jurisdictions),
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
    diagnostic = payload["national_diagnostic"]
    print(
        "national economics export: "
        f"{payload['coverage']['jurisdiction_rows']} jurisdictions, "
        f"base={diagnostic['donor_flat']['base_cad_m3']:.4f}, "
        f"sensitivity={diagnostic['stumpage_scaled']['base_cad_m3']:.4f}, "
        f"sha256={manifest['generated'][0]['sha256'][:16]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
