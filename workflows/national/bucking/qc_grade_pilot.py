"""Quebec pilot grade-rule rerun on the national bucking v1 QC tree list.

Scope
-----
This is a **pilot / assumption** product.  It reruns the ten deterministic
Quebec representative trees from ``national_bucking_pilot_v1`` through two sort
sets:

* ``dimension_only`` -- the existing QC pilot dimensions with
  ``grade=not_assigned``;
* ``grade_rules`` -- a QC pilot grade set that maps MERIS quality classes
  (DER, F1-F4, PATE, AUTRE) onto top-diameter, target-length and defect
  thresholds.

The grade mapping is **not** an official Quebec grade, MERIS product
classification, or Petro grade prediction.  It is a transparent rule-based
pilot.  Dimensions are carried as ``published_table`` evidence because they are
derived from the MERIS/Petro product references; the mapping to tree geometry
is ``assumption``.  Every log price is a MERIS current-deck proxy, not an
observed transaction.

Outputs are written under
``data/derived/national/bucking/qc_grade_pilot_v1``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .national_bucking_pilot_v1 import (
    QC_TAPER_PATH,
    PilotSortRule,
    PilotTaper,
    _sha256,
    _write_csv,
    assert_no_unexpected_deletions,
    buck_tree,
    build_taper,
    load_national_sort_rules,
)
from .pilot_schema import (
    EVIDENCE_TIERS,
    PilotTree,
    SchemaError,
    weakest_evidence,
)
from .schneider2013 import SchneiderTaperRegistry, load_taper_registry

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DIMENSION_RULES_PATH",
    "GRADE_RULES_PATH",
    "SCENARIOS",
    "generate_pilot",
    "load_qc_tree_list",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "qc_grade_pilot_v1"
)
QC_TREE_LIST_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "national_bucking_pilot_v1"
    / "tree_lists"
    / "QC.csv"
)
GRADE_RULES_PATH = (
    REPO_ROOT
    / "workflows"
    / "national"
    / "bucking"
    / "rules"
    / "qc_pilot_grade_sorts.csv"
)
DIMENSION_RULES_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "qc_pilot_sorts.csv"
)
MERIS_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "harvest_basis" / "meris_qc_full"
)
MERIS_PRICE_DECK_PATH = MERIS_DIR / "meris_price_deck.csv"
MERIS_REVENUE_CURVES_PATH = MERIS_DIR / "meris_revenue_curves.csv"
MERIS_MANIFEST_PATH = MERIS_DIR / "manifest.json"

SCENARIOS: tuple[str, ...] = ("dimension_only", "grade_rules")
QUALITY_PRODUCT = {
    "DER": "peeler",
    "F1": "sawlog",
    "F2": "sawlog",
    "F3": "sawlog",
    "F4": "sawlog",
    "PATE": "pulpwood",
    "AUTRE": "other",
}
GRADE_STATUS = "pilot_assumption"
PRICE_DECK_ID = "QC-MERIS-2025-C-PILOT"

_LABELS = {
    "model_estimate": True,
    "synthetic": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
}


def _add_labels(row: dict[str, Any]) -> dict[str, Any]:
    row.update(_LABELS)
    return row


def load_qc_tree_list(path: Path = QC_TREE_LIST_PATH) -> tuple[PilotTree, ...]:
    """Parse the committed v1 QC tree list into strict PilotTree rows."""
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"QC tree list not found: {input_path}")
    frame = pd.read_csv(input_path)
    required = {
        "tree_id",
        "site_id",
        "jurisdiction",
        "species",
        "species_group",
        "dbh_cm",
        "height_m",
        "defect_pct",
        "expansion_factor",
        "status",
        "provenance",
        "evidence",
        "taper_id",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SchemaError(f"QC tree list is missing columns: {missing}")
    trees = tuple(
        PilotTree(
            tree_id=str(row.tree_id),
            site_id=str(row.site_id),
            jurisdiction=str(row.jurisdiction),
            species=str(row.species),
            species_group=str(row.species_group),
            dbh_cm=float(row.dbh_cm),
            height_m=float(row.height_m),
            defect_pct=float(row.defect_pct),
            expansion_factor=float(row.expansion_factor),
            status=str(row.status),
            provenance=str(row.provenance),
            evidence=str(row.evidence),
            taper_id=str(row.taper_id),
        )
        for row in frame.itertuples(index=False)
    )
    if not trees:
        raise SchemaError("QC tree list is empty")
    if {tree.jurisdiction for tree in trees} != {"QC"}:
        raise SchemaError("QC grade pilot accepts only jurisdiction QC")
    return trees


def _load_rules(path: Path, expected_name: str) -> tuple[PilotSortRule, ...]:
    rules = load_national_sort_rules(path)
    if not rules:
        raise SchemaError(f"no rules loaded from {path}")
    if any(rule.jurisdiction not in {"QC", "ALL"} for rule in rules):
        raise SchemaError(f"{expected_name} contains a non-QC jurisdiction")
    return rules


def _resolve_tapers(
    trees: Sequence[PilotTree], registry: SchneiderTaperRegistry
) -> dict[str, PilotTaper]:
    tapers: dict[str, PilotTaper] = {}
    for tree in trees:
        taper = build_taper(tree.jurisdiction, tree.species, registry, {})
        if taper.taper_id != tree.taper_id:
            raise SchemaError(
                f"tree {tree.tree_id} taper_id {tree.taper_id!r} does not match "
                f"resolved taper {taper.taper_id!r}"
            )
        tapers[tree.tree_id] = taper
    return tapers


def _run_scenario(
    scenario: str,
    trees: Sequence[PilotTree],
    tapers: Mapping[str, PilotTaper],
    rules: Sequence[PilotSortRule],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    log_rows: list[dict[str, Any]] = []
    residue_rows: list[dict[str, Any]] = []
    reconciliation_rows: list[dict[str, Any]] = []
    for tree in trees:
        tree_logs, residue = buck_tree(tree, tapers[tree.tree_id], tuple(rules))
        for log in tree_logs:
            row = _add_labels(log.to_dict())
            row["scenario"] = scenario
            log_rows.append(row)
        residue_row = _add_labels(residue.to_dict())
        residue_row["scenario"] = scenario
        residue_rows.append(residue_row)
        products: dict[str, float] = {}
        for log in tree_logs:
            products[log.product] = products.get(log.product, 0.0) + log.volume_m3
        reconciliation_rows.append(
            _add_labels(
                {
                    "scenario": scenario,
                    "tree_id": tree.tree_id,
                    "jurisdiction": tree.jurisdiction,
                    "species": tree.species,
                    "dbh_cm": tree.dbh_cm,
                    "height_m": tree.height_m,
                    "defect_pct": tree.defect_pct,
                    "taper_total_m3": residue.taper_total_m3,
                    "recovered_gross_m3": residue.recovered_log_m3,
                    "residue_m3": residue.residue_volume_m3,
                    "defect_m3": sum(log.defect_deduction_m3 for log in tree_logs),
                    "recovered_net_m3": sum(log.net_volume_m3 for log in tree_logs),
                    "n_logs": len(tree_logs),
                    "sawlog_m3": products.get("sawlog", 0.0),
                    "peeler_m3": products.get("peeler", 0.0),
                    "pulpwood_m3": products.get("pulpwood", 0.0),
                    "other_m3": products.get("other", 0.0),
                    "value_cad": sum(log.value_cad for log in tree_logs),
                    "balance_error_m3": residue.balance_error_m3,
                    "evidence": weakest_evidence(
                        [residue.evidence, *(log.evidence for log in tree_logs)]
                    ),
                    "taper_evidence": residue.taper_evidence,
                    "taper_id": tree.taper_id,
                }
            )
        )
    return log_rows, residue_rows, reconciliation_rows


def _grade_distribution(log_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not log_rows:
        return []
    frame = pd.DataFrame(log_rows)
    grouped = (
        frame.groupby(
            ["scenario", "grade", "quality", "product", "sort_code", "sort_basis"],
            dropna=False,
        )
        .agg(
            n_logs=("tree_id", "size"),
            n_trees=("tree_id", "nunique"),
            volume_m3=("volume_m3", "sum"),
            net_volume_m3=("net_volume_m3", "sum"),
            value_cad=("value_cad", "sum"),
        )
        .reset_index()
        .sort_values(["scenario", "product", "grade", "sort_code"])
    )
    return grouped.to_dict("records")


def _product_summary(log_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not log_rows:
        return []
    frame = pd.DataFrame(log_rows)
    grouped = (
        frame.groupby(["scenario", "product"], dropna=False)
        .agg(
            n_logs=("tree_id", "size"),
            n_trees=("tree_id", "nunique"),
            volume_m3=("volume_m3", "sum"),
            net_volume_m3=("net_volume_m3", "sum"),
            value_cad=("value_cad", "sum"),
        )
        .reset_index()
        .sort_values(["scenario", "product"])
    )
    return grouped.to_dict("records")


def _scenario_totals(
    logs: Sequence[dict[str, Any]],
    residues: Sequence[dict[str, Any]],
    reconciliation: Sequence[dict[str, Any]],
    scenario: str,
) -> dict[str, Any]:
    products: dict[str, float] = {}
    for row in logs:
        products[row["product"]] = products.get(row["product"], 0.0) + float(
            row["volume_m3"]
        )
    taper_total = sum(float(row["taper_total_m3"]) for row in reconciliation)
    recovered = sum(float(row["recovered_gross_m3"]) for row in reconciliation)
    net = sum(float(row["recovered_net_m3"]) for row in reconciliation)
    defect = sum(float(row["defect_m3"]) for row in reconciliation)
    residue = sum(float(row["residue_m3"]) for row in reconciliation)
    residue_odt = sum(float(row["residue_odt"]) for row in residues)
    sawlog = products.get("sawlog", 0.0)
    peeler = products.get("peeler", 0.0)
    pulpwood = products.get("pulpwood", 0.0)
    other = products.get("other", 0.0)
    return {
        "scenario": scenario,
        "n_trees": len(reconciliation),
        "n_trees_with_logs": sum(1 for row in reconciliation if row["n_logs"] > 0),
        "n_logs": len(logs),
        "taper_total_m3": taper_total,
        "recovered_gross_m3": recovered,
        "recovered_net_m3": net,
        "defect_m3": defect,
        "residue_m3": residue,
        "residue_odt": residue_odt,
        "value_cad": sum(float(row["value_cad"]) for row in logs),
        "sawlog_m3": sawlog,
        "peeler_m3": peeler,
        "pulpwood_m3": pulpwood,
        "other_m3": other,
        "sawlog_plus_peeler_m3": sawlog + peeler,
        "sawlog_plus_peeler_share": _safe_div(sawlog + peeler, recovered),
        "sawlog_share": _safe_div(sawlog, recovered),
        "pulpwood_share": _safe_div(pulpwood, recovered),
        "other_share": _safe_div(other, recovered),
        "residue_m3_per_recovered_m3": _safe_div(residue, recovered),
        "residue_odt_per_recovered_m3": _safe_div(residue_odt, recovered),
        "balance_error_m3": abs(recovered + residue - taper_total),
        "net_defect_error_m3": abs(net + defect - recovered),
        "max_tree_balance_error_m3": max(
            (float(row["balance_error_m3"]) for row in reconciliation), default=0.0
        ),
    }


def _safe_div(numerator: float, denominator: float) -> float:
    if not math.isfinite(denominator) or denominator == 0.0:
        return 0.0
    return numerator / denominator


def _comparison_rows(totals: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    dimension = totals["dimension_only"]
    grade = totals["grade_rules"]
    metrics = (
        ("n_logs", "count"),
        ("n_trees_with_logs", "count"),
        ("taper_total_m3", "m3"),
        ("recovered_gross_m3", "m3"),
        ("recovered_net_m3", "m3"),
        ("defect_m3", "m3"),
        ("residue_m3", "m3"),
        ("residue_odt", "ODT"),
        ("value_cad", "CAD"),
        ("sawlog_m3", "m3"),
        ("peeler_m3", "m3"),
        ("pulpwood_m3", "m3"),
        ("other_m3", "m3"),
        ("sawlog_plus_peeler_m3", "m3"),
        ("sawlog_plus_peeler_share", "fraction"),
        ("sawlog_share", "fraction"),
        ("pulpwood_share", "fraction"),
        ("other_share", "fraction"),
        ("residue_m3_per_recovered_m3", "m3/m3"),
        ("residue_odt_per_recovered_m3", "ODT/m3"),
        ("balance_error_m3", "m3"),
        ("net_defect_error_m3", "m3"),
    )
    rows: list[dict[str, Any]] = []
    for metric, unit in metrics:
        before = float(dimension[metric])
        after = float(grade[metric])
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "dimension_only": before,
                "grade_rules": after,
                "delta": after - before,
                "relative_change": _safe_div(after - before, before),
            }
        )
    return rows


def _schema_markdown() -> str:
    return """# Quebec pilot grade-rule schema

Every row is a pilot/model row: `model_estimate=true`, `synthetic=true`,
`observed=false`, `policy_grade=false`, `national_claim=false`.

## Grade rule set

| Field | Meaning |
|---|---|
| `quality` | MERIS/Petro quality class (`DER`, `F1`-`F4`, `PATE`, `AUTRE`). |
| `grade` | Pilot label (`pilot_<quality>`), never an official grade. |
| `grade_status` | `pilot_assumption` for the grade scenario. |
| `sort_basis` | `grade_pilot_assumption` or `dimension_only`. |
| `min_top_cm` | Minimum inside-bark top diameter for the rule. |
| `target_length_m` | Candidate log length used by the bucker. |
| `max_defect_pct` | Maximum tree defect percentage eligible for the class. |
| `price_cad_m3` | Pilot price, CAD per net m3. |
| `price_deck_id` | `QC-MERIS-2025-C-PILOT`. |
| `price_reference` | Source row/statistic in the MERIS deck. |
| `dimension_evidence` | `published_table` for the MERIS/Petro dimensions. |
| `mapping_evidence` | `assumption` for the quality mapping. |
| `evidence` | Weakest of dimension and mapping evidence. |

## Outputs

| File | Grain |
|---|---|
| `tree_list.csv` | One row per input QC tree. |
| `bucked_logs.csv` | Scenario x recovered log. |
| `residue_ledger.csv` | Scenario x stem. |
| `reconciliation.csv` | Scenario x stem. |
| `grade_distribution.csv` | Scenario x grade x quality x product x sort. |
| `product_summary.csv` | Scenario x product. |
| `scenario_comparison.csv` | Metric x scenario. |
| `volume_reconciliation.csv` | Scenario totals. |
| `grade_rules.csv` | Grade rule set used. |
| `dimension_rules.csv` | Dimension-only comparator. |
| `summary.json` | Machine-readable totals, checks and limitations. |
| `manifest.json` | Input/output hashes and configuration. |
"""


def generate_pilot(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Run dimension-only and pilot-grade scenarios on the QC tree list."""
    deletions = assert_no_unexpected_deletions()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    trees = load_qc_tree_list(QC_TREE_LIST_PATH)
    registry = load_taper_registry(QC_TAPER_PATH)
    tapers = _resolve_tapers(trees, registry)
    dimension_rules = _load_rules(DIMENSION_RULES_PATH, "dimension rules")
    grade_rules = _load_rules(GRADE_RULES_PATH, "grade rules")

    if not any(rule.grade_status == GRADE_STATUS for rule in grade_rules):
        raise SchemaError("grade rule set contains no pilot_assumption grades")
    if any(rule.grade_status != "not_assigned" for rule in dimension_rules):
        raise SchemaError("dimension-only rule set must not assign a grade")

    all_logs: list[dict[str, Any]] = []
    all_residues: list[dict[str, Any]] = []
    all_reconciliation: list[dict[str, Any]] = []
    rules_by_scenario = {
        "dimension_only": dimension_rules,
        "grade_rules": grade_rules,
    }
    for scenario in SCENARIOS:
        logs, residues, reconciliation = _run_scenario(
            scenario, trees, tapers, rules_by_scenario[scenario]
        )
        all_logs.extend(logs)
        all_residues.extend(residues)
        all_reconciliation.extend(reconciliation)

    totals = {
        scenario: _scenario_totals(
            [row for row in all_logs if row["scenario"] == scenario],
            [row for row in all_residues if row["scenario"] == scenario],
            [row for row in all_reconciliation if row["scenario"] == scenario],
            scenario,
        )
        for scenario in SCENARIOS
    }
    comparison = _comparison_rows(totals)
    grade_distribution = _grade_distribution(all_logs)
    product_summary = _product_summary(all_logs)
    volume_reconciliation = [
        {
            "scenario": scenario,
            **{
                key: totals[scenario][key]
                for key in (
                    "n_trees",
                    "n_trees_with_logs",
                    "n_logs",
                    "taper_total_m3",
                    "recovered_gross_m3",
                    "recovered_net_m3",
                    "defect_m3",
                    "residue_m3",
                    "balance_error_m3",
                    "net_defect_error_m3",
                    "max_tree_balance_error_m3",
                )
            },
        }
        for scenario in SCENARIOS
    ]

    tree_rows = [_add_labels(tree.to_dict()) for tree in trees]

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    add_check(
        "ten_qc_trees",
        len(trees) == 10,
        f"{len(trees)} QC trees loaded from the v1 tree list",
    )
    add_check(
        "same_geometry",
        all(
            abs(
                next(
                    float(row["taper_total_m3"])
                    for row in all_reconciliation
                    if row["scenario"] == "dimension_only"
                    and row["tree_id"] == tree.tree_id
                )
                - next(
                    float(row["taper_total_m3"])
                    for row in all_reconciliation
                    if row["scenario"] == "grade_rules"
                    and row["tree_id"] == tree.tree_id
                )
            )
            < 1e-12
            for tree in trees
        ),
        "both scenarios use the same taper total for every tree",
    )
    add_check(
        "volume_balance",
        all(
            totals[scenario]["balance_error_m3"] <= 1e-8
            and totals[scenario]["net_defect_error_m3"] <= 1e-8
            for scenario in SCENARIOS
        ),
        "; ".join(
            f"{scenario}={totals[scenario]['balance_error_m3']:.3g}"
            for scenario in SCENARIOS
        ),
    )
    add_check(
        "grade_mapping",
        all(
            rule.grade_status == GRADE_STATUS
            and rule.grade.startswith("pilot_")
            and rule.quality in QUALITY_PRODUCT
            and rule.product == QUALITY_PRODUCT[rule.quality]
            for rule in grade_rules
        ),
        f"{len(grade_rules)} grade rules map quality to product",
    )
    add_check(
        "dimension_not_assigned",
        all(
            log["grade"] == "not_assigned"
            and log["grade_status"] == "not_assigned"
            and log["quality"] == "not_classified"
            and log["sort_basis"] == "dimension_only"
            for log in all_logs
            if log["scenario"] == "dimension_only"
        ),
        "dimension-only logs have no grade or quality class",
    )
    add_check(
        "grade_rows_labelled",
        all(
            log["grade_status"] == GRADE_STATUS
            and log["grade"] != "not_assigned"
            and log["quality"] in QUALITY_PRODUCT
            and log["sort_basis"] == "grade_pilot_assumption"
            for log in all_logs
            if log["scenario"] == "grade_rules"
        ),
        "all grade-rule logs carry a pilot grade and quality class",
    )
    add_check(
        "pilot_labels",
        all(
            row["model_estimate"]
            and row["synthetic"]
            and not row["observed"]
            and not row["policy_grade"]
            and not row["national_claim"]
            for row in tree_rows + all_logs + all_residues + all_reconciliation
        ),
        "model_estimate/synthetic/observed=false/policy_grade=false/national_claim=false",
    )
    numeric_frames = [
        pd.DataFrame(tree_rows),
        pd.DataFrame(all_logs),
        pd.DataFrame(all_residues),
        pd.DataFrame(all_reconciliation),
        pd.DataFrame(grade_distribution),
        pd.DataFrame(product_summary),
        pd.DataFrame(volume_reconciliation),
    ]
    add_check(
        "no_nan",
        all(
            not frame.select_dtypes(include="number").isna().any().any()
            for frame in numeric_frames
        ),
        "numeric columns contain no NaN",
    )
    add_check(
        "no_negative",
        all(
            (frame.select_dtypes(include="number") >= 0.0).all().all()
            for frame in numeric_frames
        ),
        "volumes, residues and values are non-negative",
    )
    add_check(
        "evidence_tiers",
        all(log["taper_evidence"] == "published_table" for log in all_logs)
        and all(log["sort_evidence"] == "assumption" for log in all_logs)
        and all(
            rule.dimension_evidence == "published_table"
            and rule.mapping_evidence == "assumption"
            for rule in grade_rules
        ),
        "taper published_table; dimensions published_table; mapping/prices assumption",
    )
    add_check(
        "repository_hygiene",
        not deletions,
        "no unexpected D in parent or data git status before run",
    )

    failed = [check for check in checks if check["status"] != "PASS"]
    if failed:
        raise SchemaError(f"QC grade pilot checks failed: {failed}")

    summary = {
        "artifact": "qc_grade_pilot_v1",
        "schema_version": "qc_grade_pilot_v1",
        "scope": "quebec_representative_synthetic_grade_pilot",
        "official_grade_claim": False,
        "grade_status": GRADE_STATUS,
        "national_claim": False,
        "labels": {
            **_LABELS,
            "ui_or_public_osecan_changed": False,
        },
        "counts": {
            "trees": len(trees),
            "scenarios": len(SCENARIOS),
            "logs": len(all_logs),
            "residue_rows": len(all_residues),
            "reconciliation_rows": len(all_reconciliation),
            "grade_rules": len(grade_rules),
            "dimension_rules": len(dimension_rules),
        },
        "scenario_totals": totals,
        "comparison": comparison,
        "checks": checks,
        "limitations": [
            "Ten deterministic representative trees, not a Quebec inventory or PSP sample.",
            "The dimension-only comparator is qc_pilot_sorts.csv, not the v1 national sort set used by the earlier 13-jurisdiction run.",
            "The quality/grade mapping is a pilot rule set, not an official Quebec grade, MERIS classification, or Petro prediction.",
            "Prices are MERIS current-deck proxies, not observed transactions.",
            "AUTRE is carried as product 'other' in this bucking contract, not as MERIS 'mixed_log'.",
            "Defect percentage is used as a quality proxy; it is not a measured grade defect.",
            "Density, moisture, chip and hog yields remain pilot assumptions.",
            "No UI or public OSeCAN surface was changed.",
        ],
    }

    log_columns = list(all_logs[0])
    residue_columns = list(all_residues[0])
    reconciliation_columns = list(all_reconciliation[0])
    _write_csv(output / "tree_list.csv", tree_rows, list(tree_rows[0]))
    _write_csv(output / "bucked_logs.csv", all_logs, log_columns)
    _write_csv(output / "residue_ledger.csv", all_residues, residue_columns)
    _write_csv(
        output / "reconciliation.csv", all_reconciliation, reconciliation_columns
    )
    _write_csv(
        output / "grade_distribution.csv",
        grade_distribution,
        list(grade_distribution[0]),
    )
    _write_csv(
        output / "product_summary.csv", product_summary, list(product_summary[0])
    )
    _write_csv(output / "scenario_comparison.csv", comparison, list(comparison[0]))
    _write_csv(
        output / "volume_reconciliation.csv",
        volume_reconciliation,
        list(volume_reconciliation[0]),
    )
    _write_csv(
        output / "grade_rules.csv",
        [asdict(rule) for rule in grade_rules],
        list(asdict(grade_rules[0])),
    )
    _write_csv(
        output / "dimension_rules.csv",
        [asdict(rule) for rule in dimension_rules],
        list(asdict(dimension_rules[0])),
    )
    (output / "schema.md").write_text(_schema_markdown(), encoding="utf-8")
    (output / "report.md").write_text(
        _render_report(grade_rules, totals, comparison, checks), encoding="utf-8"
    )

    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    output_files = [
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "grade_distribution.csv",
        "product_summary.csv",
        "scenario_comparison.csv",
        "volume_reconciliation.csv",
        "grade_rules.csv",
        "dimension_rules.csv",
        "schema.md",
        "report.md",
        "summary.json",
    ]
    manifest = {
        "artifact": "qc_grade_pilot_v1",
        "schema_version": "qc_grade_pilot_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "scope": "quebec_representative_synthetic_grade_pilot",
        "official_grade_claim": False,
        "grade_status": GRADE_STATUS,
        "national_claim": False,
        "labels": summary["labels"],
        "command": (
            ".venv/bin/python -m workflows.national.bucking.qc_grade_pilot "
            "--out-dir data/derived/national/bucking/qc_grade_pilot_v1"
        ),
        "config": {
            "scenarios": list(SCENARIOS),
            "tree_list": QC_TREE_LIST_PATH.relative_to(REPO_ROOT).as_posix(),
            "price_deck_id": PRICE_DECK_ID,
            "quality_product_map": QUALITY_PRODUCT,
            "stump_height_m": 0.3,
            "bucker": "value-maximising dynamic program over target lengths",
        },
        "inputs": {
            "qc_tree_list": {
                "path": QC_TREE_LIST_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(QC_TREE_LIST_PATH),
            },
            "qc_taper_registry": {
                "path": QC_TAPER_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(QC_TAPER_PATH),
            },
            "grade_rules": {
                "path": GRADE_RULES_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(GRADE_RULES_PATH),
            },
            "dimension_rules": {
                "path": DIMENSION_RULES_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(DIMENSION_RULES_PATH),
            },
            "meris_price_deck": {
                "path": MERIS_PRICE_DECK_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(MERIS_PRICE_DECK_PATH),
            },
            "meris_revenue_curves": {
                "path": MERIS_REVENUE_CURVES_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(MERIS_REVENUE_CURVES_PATH),
            },
            "meris_manifest": {
                "path": MERIS_MANIFEST_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(MERIS_MANIFEST_PATH),
            },
        },
        "code": [
            "workflows/national/bucking/qc_grade_pilot.py",
            "workflows/national/bucking/national_bucking_pilot_v1.py",
            "workflows/national/bucking/pilot_schema.py",
            "workflows/national/bucking/schneider2013.py",
            "workflows/national/bucking/rules/qc_pilot_grade_sorts.csv",
            "workflows/national/bucking/rules/qc_pilot_sorts.csv",
            "workflows/national/bucking/rules/qc_schneider2013_taper.csv",
        ],
        "counts": summary["counts"],
        "scenario_totals": totals,
        "comparison": comparison,
        "checks": checks,
        "limitations": summary["limitations"],
        "outputs": [
            {
                "path": name,
                "sha256": _sha256(output / name),
                "bytes": (output / name).stat().st_size,
            }
            for name in output_files
        ],
        "evidence_tiers": list(EVIDENCE_TIERS),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _render_report(
    grade_rules: Sequence[PilotSortRule],
    totals: Mapping[str, Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, Any]],
) -> str:
    dimension = totals["dimension_only"]
    grade = totals["grade_rules"]
    lines = [
        "# Quebec pilot grade-rule rerun",
        "",
        "**Pilot / assumption. Not an official Quebec grade, MERIS classification",
        "or operational supply estimate.**",
        "",
        "The rerun uses the ten committed v1 Quebec representative trees, the",
        "published Schneider (2013) taper and two sort sets on identical geometry.",
        "The dimension-only comparator is `qc_pilot_sorts.csv`; the earlier v1",
        "13-jurisdiction run used `national_pilot_sorts.csv` and is not the",
        "comparator here.",
        "",
        "## Grade rule set",
        "",
        f"`{GRADE_RULES_PATH.name}` contains {len(grade_rules)} rules.  The quality",
        "class is a pilot mapping from top DIB, target length and tree defect",
        "percentage.  Dimensions are `published_table`; mapping and prices are",
        "`assumption`.",
        "",
        "| Quality | Product | Grade label | Top min (cm) | Defect max (%) |",
        "|---|---|---|---:|---:|",
    ]
    seen: set[str] = set()
    for rule in grade_rules:
        if rule.quality in seen:
            continue
        seen.add(rule.quality)
        lines.append(
            f"| {rule.quality} | {rule.product} | {rule.grade} | "
            f"{rule.min_top_cm:.1f} | {rule.max_defect_pct:.1f} |"
        )
    lines += [
        "",
        "## QC grade results",
        "",
        "| Metric | Dimension-only | Grade rules | Delta |",
        "|---|---:|---:|---:|",
    ]
    for row in comparison:
        lines.append(
            f"| {row['metric']} | {row['dimension_only']:.6g} | "
            f"{row['grade_rules']:.6g} | {row['delta']:+.6g} |"
        )
    lines += [
        "",
        "Log-level results are in `bucked_logs.csv`; grade distribution in",
        "`grade_distribution.csv`; residue in `residue_ledger.csv` and",
        "`volume_reconciliation.csv`.",
        "",
        "## Dimension-only vs grade",
        "",
        (
            f"- Sawlog + peeler share: {dimension['sawlog_plus_peeler_share']:.3f} "
            f"vs {grade['sawlog_plus_peeler_share']:.3f}."
        ),
        (
            f"- Pulpwood share: {dimension['pulpwood_share']:.3f} vs "
            f"{grade['pulpwood_share']:.3f}."
        ),
        (
            f"- Residue: {dimension['residue_m3']:.6g} m3 vs "
            f"{grade['residue_m3']:.6g} m3."
        ),
        (f"- Value: {dimension['value_cad']:.3f} CAD vs {grade['value_cad']:.3f} CAD."),
        "",
        "Both scenarios satisfy taper total = recovered gross + residue and",
        "recovered gross = recovered net + defect, per tree and in total.",
        "",
        "## Evidence and caveats",
        "",
        "- Taper geometry: `published_table` (Schneider 2013).",
        "- Rule dimensions: `published_table` from MERIS/Petro product references.",
        "- Quality mapping, tree geometry, prices, density, moisture and chip/hog",
        "  yields: `assumption`.",
        "- In this bucking contract AUTRE is carried as product `other`; it is not",
        "  the MERIS mixed-log product class.",
        "- Ten representative trees, not inventory; no official grade claim.",
        "- No UI or public OSeCAN surface was changed.",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        lines.append(f"- `{check['check_id']}`: {check['status']} — {check['detail']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    manifest = generate_pilot(args.out_dir)
    grade = manifest["scenario_totals"]["grade_rules"]
    dimension = manifest["scenario_totals"]["dimension_only"]
    print(
        f"qc grade pilot wrote {args.out_dir} | trees={manifest['counts']['trees']} "
        f"logs={manifest['counts']['logs']} "
        f"grade_value={grade['value_cad']:.3f} CAD "
        f"dimension_value={dimension['value_cad']:.3f} CAD "
        f"grade_sawpeeler={grade['sawlog_plus_peeler_share']:.3f} "
        f"dimension_sawpeeler={dimension['sawlog_plus_peeler_share']:.3f} "
        f"balance_error={max(grade['balance_error_m3'], dimension['balance_error_m3']):.3g} m3"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
