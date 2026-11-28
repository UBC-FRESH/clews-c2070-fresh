"""Rerun the sustainable and operable WS4 cases on transformed RSYC curves.

The national 990-m WS4 dataset is read-only.  This driver reuses the existing
100/150 sustainable scheduler and the share-based operable scheduler, changing
only the dataset path to ``scanfi-national-990m-transformed``.  The 100, 119,
and 150 Mm3/yr controls, the 100-year horizon, the 10-year periods, the MAI
rotation band (40-200 years), the oldest-first whole-block selection, the
annual longest-processing-time assignment, and the ``screen_core30``
province-share mask are unchanged.

Frozen chain v5.1 coefficients are applied to the first-period and 100-year
achieved harvest by origin province, and the existing WS4 OSeCAN handoff logic
is rebuilt for the transformed chains.  The official OSeCAN tree, frozen chain
v5.1, the original 990-m dataset, and the original-curve case artifacts are
read-only.

All outputs are model estimates.  This is not an AAC, a legal THLB, a slope or
spatial road screen, an official OSeCAN change, a policy result, or a national
supply claim.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflows.national.facilities import ws4_chain_119m as chain
from workflows.national.facilities import ws4_operable_cases as operable
from workflows.national.facilities import ws4_operable_cases_osecan as operable_osecan
from workflows.national.facilities import ws4_sustainable_119m as base
from workflows.national.facilities import ws4_sustainable_cases as cases
from workflows.national.facilities import (
    ws4_sustainable_cases_osecan as sustainable_osecan,
)

REPO_ROOT = base.REPO_ROOT
DATA_ROOT = base.DATA_ROOT
TRANSFORMED_ROOT = DATA_ROOT / "derived/ws4/scanfi-national-990m-transformed"
DATASET_DIR = TRANSFORMED_ROOT / "dataset"
OPERABLE_MASK_DIR = TRANSFORMED_ROOT / "operable-mask"
CASES_ROOT = DATA_ROOT / "derived/national/facilities/ws4_transformed_cases"
SUSTAINABLE_CHAIN_ROOT = CASES_ROOT / "sustainable"
OPERABLE_CHAIN_ROOT = CASES_ROOT / "operable"
OSECAN_ROOT = CASES_ROOT / "osecan"

ORIGINAL_DATASET = DATA_ROOT / "derived/ws4/scanfi-national-990m/dataset"
ORIGINAL_SUSTAINABLE_CASES = (
    DATA_ROOT / "derived/national/facilities/ws4_sustainable_cases"
)
ORIGINAL_SUSTAINABLE_119 = (
    DATA_ROOT / "derived/national/facilities/ws4_sustainable_119m"
)
ORIGINAL_OPERABLE_CASES = DATA_ROOT / "derived/national/facilities/ws4_operable_cases"
ORIGINAL_OPERABLE_MASK = DATA_ROOT / "derived/ws4/operable-mask"
TRANSFORMED_119_SUMMARY = (
    DATA_ROOT / "derived/national/facilities/ws4_transformed_119m/summary.json"
)

ARTIFACT = "ws4_transformed_cases"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = (
    "Sustainable 100/119/150 Mm3/yr and operable WS4 cases on transformed RSYC "
    "curves"
)
REPORT_NAME = "WS4_TRANSFORMED_CASES.md"

TARGET_100M = 100_000_000.1
TARGET_119M = 118_977_339.1
TARGET_150M = 150_000_000.2

LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "sustainable_schedule": True,
    "transformed_curves": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}
ROW_LABELS: dict[str, str] = {
    key: "true" if value else "false" for key, value in LABELS.items()
}
OPERABLE_LABELS: dict[str, str] = {
    "share_based_operable_proxy": "true",
    "legal_thlb": "false",
    "slope_screen": "false",
}

CAVEATS: tuple[str, ...] = (
    (
        "Yield curves are the national RSYC transform v1: V100 is preserved, "
        "ages 1-200 are transformed, and ages 201-600 are held at the age-200 "
        "value. The WS4 model curve is the forest-area-weighted mean of "
        "transformed cell curves."
    ),
    (
        "The scheduler, provincial control shares, 100-year horizon, 10-year "
        "periods, MAI rotation band (40-200 years), oldest-first whole-block "
        "selection, and annual longest-processing-time assignment are unchanged."
    ),
    (
        "The full-forest sustainable cases use the whole WS4 estate. The "
        "operable cases use the existing screen_core30 province-share mask; it "
        "is not a legal THLB, a slope screen, or a spatial road overlay."
    ),
    (
        "Whole blocks are selected and never split or scaled; a final block may "
        "overshoot a provincial target. No block is harvested below rotation and "
        "rotation is never relaxed to meet a target."
    ),
    (
        "Frozen chain v5.1 coefficients are applied to WS4 harvest by origin "
        "province. Facility assignment, mill capacity, transport, and "
        "interprovincial trade are not re-run."
    ),
    (
        "The OSeCAN handoff applies frozen handoff-v10 zone weights and the "
        "official tier thresholds. It is a model estimate, not an official "
        "OSeCAN change."
    ),
    (
        "No original national 990 m dataset, original-curve case artifact, "
        "frozen chain v5.1 artifact, official OSeCAN file, or UI surface is "
        "changed."
    ),
)


class Ws4TransformedCasesError(RuntimeError):
    """Raised when a transformed case or handoff fails a guardrail."""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    base._write_json(path, payload)


def _write_csv(
    path: Path, fields: tuple[str, ...] | list[str], rows: list[dict[str, Any]]
) -> None:
    base._write_csv(path, fields, rows)


def _sha256(path: Path) -> str:
    return base._sha256(path)


def _relative(path: Path) -> str:
    return base._relative(path)


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _target_by_key() -> dict[str, float]:
    return {
        "100m": TARGET_100M,
        "119m": TARGET_119M,
        "150m": TARGET_150M,
    }


def _sustainable_specs() -> tuple[cases.CaseSpec, ...]:
    targets = _target_by_key()
    return tuple(
        cases.CaseSpec(
            key=key,
            target_annual_m3=targets[key],
            primary_basis=f"sustainable_{key}",
            control_csv=(
                DATA_ROOT
                / "derived/national/harvest_basis/provincial_allocation"
                / f"{key}.csv"
            ),
            output_dir=TRANSFORMED_ROOT / f"sustainable-{key}",
            chain_dir=SUSTAINABLE_CHAIN_ROOT / key,
            artifact=f"ws4_transformed_sustainable_{key}",
            artifact_title=(
                f"Sustainable {key.replace('m', ' Mm3/yr')} WS4 schedule and "
                "chain on transformed RSYC curves"
            ),
            report_name=f"WS4_TRANSFORMED_SUSTAINABLE_{key.upper()}.md",
            dataset_dir=DATASET_DIR,
        )
        for key in ("100m", "150m")
    )


def _full_forest_summary_path(key: str) -> Path:
    if key == "119m":
        return TRANSFORMED_119_SUMMARY
    return SUSTAINABLE_CHAIN_ROOT / key / "summary.json"


def _operable_specs() -> tuple[operable.OperableCaseSpec, ...]:
    targets = _target_by_key()
    return tuple(
        operable.OperableCaseSpec(
            key=key,
            target_annual_m3=targets[key],
            primary_basis=f"operable_{key}",
            control_csv=(
                DATA_ROOT
                / "derived/national/harvest_basis/provincial_allocation"
                / f"{key}.csv"
            ),
            output_dir=TRANSFORMED_ROOT / f"operable-{key}",
            chain_dir=OPERABLE_CHAIN_ROOT / key,
            artifact=f"ws4_transformed_operable_{key}",
            artifact_title=(
                f"Sustainable operable {key.replace('m', ' Mm3/yr')} WS4 "
                "schedule and chain on transformed RSYC curves"
            ),
            report_name=f"WS4_TRANSFORMED_OPERABLE_{key.upper()}.md",
            dataset_dir=DATASET_DIR,
            mask_dir=OPERABLE_MASK_DIR,
            full_forest_summary=_full_forest_summary_path(key),
        )
        for key in ("100m", "119m", "150m")
    )


SUSTAINABLE_SPECS = _sustainable_specs()
OPERABLE_SPECS = _operable_specs()
SUSTAINABLE_BY_KEY = {spec.key: spec for spec in SUSTAINABLE_SPECS}
OPERABLE_BY_KEY = {spec.key: spec for spec in OPERABLE_SPECS}


def _add_transformed_label(payload: Any) -> None:
    """Add ``transformed_curves`` to every labelled mapping in a payload."""
    if isinstance(payload, dict):
        if "model_estimate" in payload:
            payload["transformed_curves"] = True
        for value in payload.values():
            _add_transformed_label(value)
    elif isinstance(payload, list):
        for value in payload:
            _add_transformed_label(value)


def _relabel_report(path: Path) -> None:
    """Append the transformed-curve label to a case report label block."""
    text = path.read_text(encoding="utf-8")
    marker = "`official_osecan_changed=false`, `chain_v51_unchanged=true`."
    replacement = (
        "`transformed_curves=true`, `official_osecan_changed=false`,\n"
        "`chain_v51_unchanged=true`."
    )
    if marker not in text:
        raise Ws4TransformedCasesError(f"report label block is missing in {path}")
    path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")


def _finalize_case(
    spec: cases.CaseSpec | operable.OperableCaseSpec, summary: dict[str, Any]
) -> None:
    """Add transformed labels to one case chain and rebuild its manifest."""
    for path in sorted(spec.chain_dir.glob("*.csv")):
        frame = pd.read_csv(path)
        frame["transformed_curves"] = "true"
        frame.to_csv(path, index=False, lineterminator="\n")
    _add_transformed_label(summary)
    summary["labels"] = {**summary.get("labels", {}), **LABELS}
    summary["chain_files"] = {
        name: _sha256(spec.chain_dir / name)
        for name in (
            "sustainable_harvest_by_province.csv",
            "sustainable_product_chain.csv",
            "comparison_vs_v51.csv",
        )
    }
    _write_json(spec.chain_dir / "summary.json", summary)
    _relabel_report(spec.chain_dir / spec.report_name)
    if isinstance(spec, operable.OperableCaseSpec):
        operable._write_case_manifest(spec, summary, _mask_summary())
    else:
        cases._write_case_manifest(spec, summary)


def _mask_summary() -> dict[str, Any]:
    return _read_json(OPERABLE_MASK_DIR / "mask_summary.json")


def _assert_mask_matches_original() -> None:
    path = ORIGINAL_OPERABLE_MASK / "operable_mask.parquet"
    if not path.is_file():
        raise Ws4TransformedCasesError(f"original operable mask is missing: {path}")
    original = pd.read_parquet(path, columns=["block_id", "operable_block"])
    transformed = pd.read_parquet(
        OPERABLE_MASK_DIR / "operable_mask.parquet",
        columns=["block_id", "operable_block"],
    )
    transformed = transformed.sort_values("block_id", kind="mergesort")
    original = original.sort_values("block_id", kind="mergesort")
    if not np.array_equal(
        original["block_id"].to_numpy(dtype=np.int64),
        transformed["block_id"].to_numpy(dtype=np.int64),
    ):
        raise Ws4TransformedCasesError(
            "transformed operable mask block ids differ from the original mask"
        )
    if not np.array_equal(
        original["operable_block"].to_numpy(dtype=bool),
        transformed["operable_block"].to_numpy(dtype=bool),
    ):
        raise Ws4TransformedCasesError(
            "transformed operable mask flags differ from the original mask"
        )


def _relabel_osecan_tree(root: Path) -> dict[str, Any]:
    """Add transformed labels to one OSeCAN handoff artifact."""
    for path in sorted(root.rglob("*.csv")):
        frame = pd.read_csv(path)
        frame["transformed_curves"] = "true"
        frame.to_csv(path, index=False, lineterminator="\n")
    summary_path = root / "summary.json"
    summary = _read_json(summary_path)
    _add_transformed_label(summary)
    summary["labels"] = {**summary.get("labels", {}), **LABELS}
    _write_json(summary_path, summary)
    report = next(root.glob("FORESTRY_OSECAN_HANDOFF_*.md"))
    _relabel_report(report)
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        manifest["labels"] = {**manifest.get("labels", {}), **LABELS}
        manifest["outputs"] = {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in sorted(root.iterdir())
            if path.is_file() and path.name != "manifest.json"
        }
        _write_json(manifest_path, manifest)
    return summary


def _rotation_area_weighted_mean(selected_path: Path) -> float:
    selected = pd.read_csv(selected_path)
    return float(
        np.average(
            selected["rotation_years"].to_numpy(dtype=float),
            weights=selected["area_ha"].to_numpy(dtype=float),
        )
    )


def _sustainable_comparison(
    summaries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in SUSTAINABLE_SPECS:
        summary = summaries[spec.key]
        for row in summary["chain"]["comparison"]:
            rows.append(
                {
                    "case": spec.key,
                    "metric": row["metric"],
                    "unit": row["unit"],
                    "chain_v51_value": row["chain_v51_value"],
                    "primary_basis": spec.primary_basis,
                    "primary_value": row[f"{spec.primary_basis}_value"],
                    "primary_delta": row[f"{spec.primary_basis}_delta"],
                    "primary_delta_pct": row[f"{spec.primary_basis}_delta_pct"],
                    "achieved_value": row[f"{cases.SECONDARY_BASIS}_value"],
                    "achieved_delta": row[f"{cases.SECONDARY_BASIS}_delta"],
                    "achieved_delta_pct": row[
                        f"{cases.SECONDARY_BASIS}_delta_pct"
                    ],
                    **ROW_LABELS,
                }
            )
    return rows


def _sustainable_vs_original(
    summaries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metrics = (
        ("annual_min_m3", "m3/yr", "schedule"),
        ("annual_max_m3", "m3/yr", "schedule"),
        ("annual_mean_m3", "m3/yr", "schedule"),
        ("annual_cv", "fraction", "schedule"),
        ("period_shortfall_m3", "m3", "schedule"),
        ("selected_blocks", "blocks", "schedule"),
        ("selected_area_ha", "ha", "schedule"),
        ("blocks_reharvested", "blocks", "schedule"),
    )
    rows: list[dict[str, Any]] = []
    for spec in SUSTAINABLE_SPECS:
        original = _read_json(ORIGINAL_SUSTAINABLE_CASES / spec.key / "summary.json")
        transformed = summaries[spec.key]
        for metric, unit, section in metrics:
            original_value = float(original[section][metric])
            transformed_value = float(transformed[section][metric])
            rows.append(
                {
                    "case": spec.key,
                    "metric": metric,
                    "unit": unit,
                    "original_rsyc_value": original_value,
                    "transformed_rsyc_value": transformed_value,
                    "delta": transformed_value - original_value,
                    "delta_pct": (
                        (transformed_value - original_value) / original_value * 100.0
                        if original_value
                        else 0.0
                    ),
                    **ROW_LABELS,
                }
            )
        original_rotation = _rotation_area_weighted_mean(
            DATA_ROOT / "derived/ws4" / f"sustainable-{spec.key}" / "selected_blocks.csv"
        )
        transformed_rotation = _rotation_area_weighted_mean(
            spec.output_dir / "selected_blocks.csv"
        )
        rows.append(
            {
                "case": spec.key,
                "metric": "mean_rotation_years_area_weighted",
                "unit": "years",
                "original_rsyc_value": original_rotation,
                "transformed_rsyc_value": transformed_rotation,
                "delta": transformed_rotation - original_rotation,
                "delta_pct": (
                    (transformed_rotation - original_rotation)
                    / original_rotation
                    * 100.0
                ),
                **ROW_LABELS,
            }
        )
    return rows


def _operable_vs_original_operable(
    summaries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    original = _read_json(ORIGINAL_OPERABLE_CASES / "summary.json")
    metrics = (
        ("annual_min_m3", "m3/yr"),
        ("annual_max_m3", "m3/yr"),
        ("annual_mean_m3", "m3/yr"),
        ("period_shortfall_m3", "m3"),
        ("province_shortfall_m3", "m3"),
        ("selected_blocks", "blocks"),
        ("selected_area_ha", "ha"),
    )
    rows: list[dict[str, Any]] = []
    for spec in OPERABLE_SPECS:
        original_case = original["cases"][spec.key]
        transformed = summaries[spec.key]
        for metric, unit in metrics:
            original_value = float(original_case["schedule"][metric])
            transformed_value = float(transformed["schedule"][metric])
            rows.append(
                {
                    "case": spec.key,
                    "metric": metric,
                    "unit": unit,
                    "original_curve_operable_value": original_value,
                    "transformed_curve_operable_value": transformed_value,
                    "delta": transformed_value - original_value,
                    "delta_pct": (
                        (transformed_value - original_value) / original_value * 100.0
                        if original_value
                        else 0.0
                    ),
                    **ROW_LABELS,
                    **OPERABLE_LABELS,
                }
            )
        for metric, unit in (
            ("max_nominal_control_m3", "m3/yr"),
            ("max_operable_harvest_m3", "m3/yr"),
        ):
            original_value = float(original_case["max_sustainable"][metric])
            transformed_value = float(transformed["max_sustainable"][metric])
            rows.append(
                {
                    "case": spec.key,
                    "metric": metric,
                    "unit": unit,
                    "original_curve_operable_value": original_value,
                    "transformed_curve_operable_value": transformed_value,
                    "delta": transformed_value - original_value,
                    "delta_pct": (
                        (transformed_value - original_value) / original_value * 100.0
                        if original_value
                        else 0.0
                    ),
                    **ROW_LABELS,
                    **OPERABLE_LABELS,
                }
            )
    return rows


def _write_combined_report(summary: Mapping[str, Any]) -> None:
    sustainable = summary["sustainable"]
    schedule_rows = [
        [
            spec.key,
            f"{sustainable[spec.key]['target']['annual_m3'] / 1e6:.6f}",
            f"{sustainable[spec.key]['schedule']['annual_min_m3'] / 1e6:.4f}",
            f"{sustainable[spec.key]['schedule']['annual_max_m3'] / 1e6:.4f}",
            f"{sustainable[spec.key]['schedule']['annual_mean_m3'] / 1e6:.4f}",
            f"{sustainable[spec.key]['schedule']['period_shortfall_m3'] / 1e6:.6f}",
            str(sustainable[spec.key]["schedule"]["sustained"]),
            f"{sustainable[spec.key]['schedule']['selected_blocks']:,}",
            f"{sustainable[spec.key]['schedule']['selected_area_ha'] / 1e6:.3f}",
            f"{sustainable[spec.key]['rotation_mean_years']:.3f}",
        ]
        for spec in SUSTAINABLE_SPECS
    ]
    sustainable_table = _md_table(
        [
            "Case",
            "Target Mm3/yr",
            "Annual min Mm3/yr",
            "Annual max Mm3/yr",
            "Annual mean Mm3/yr",
            "Period shortfall Mm3",
            "Sustained",
            "Blocks",
            "Area Mha",
            "Mean rotation yr",
        ],
        schedule_rows,
    )
    key_metrics = (
        "harvest_m3",
        "solid_product_m3",
        "pulp_product_odt",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_r060_pj",
        "total_residue_gross_pj",
    )
    chain_rows = [
        [
            row["case"],
            row["metric"],
            row["unit"],
            f"{float(row['chain_v51_value']):,.6f}",
            f"{float(row['primary_value']):,.6f}",
            f"{float(row['primary_delta_pct']):+.4f}",
            f"{float(row['achieved_value']):,.6f}",
            f"{float(row['achieved_delta_pct']):+.4f}",
        ]
        for row in summary["comparison_sustainable_vs_v51"]
        if row["metric"] in key_metrics
    ]
    chain_table = _md_table(
        [
            "Case",
            "Metric",
            "Unit",
            "v5.1",
            "First period",
            "Delta %",
            "Achieved",
            "Delta %",
        ],
        chain_rows,
    )
    operable_rows = [
        [
            spec.key,
            f"{operable_summary['target']['annual_m3'] / 1e6:.6f}",
            f"{operable_summary['schedule']['annual_min_m3'] / 1e6:.4f}",
            f"{operable_summary['schedule']['annual_max_m3'] / 1e6:.4f}",
            f"{operable_summary['schedule']['annual_mean_m3'] / 1e6:.4f}",
            f"{operable_summary['schedule']['period_shortfall_m3'] / 1e6:.4f}",
            f"{operable_summary['schedule']['province_shortfall_m3'] / 1e6:.4f}",
            str(operable_summary["schedule"]["sustained"]),
            str(operable_summary["schedule"]["sustained_covered_provinces"]),
            f"{operable_summary['schedule']['selected_blocks']:,}",
            f"{operable_summary['schedule']['selected_area_ha'] / 1e6:.3f}",
            f"{operable_summary['max_sustainable']['max_nominal_control_m3'] / 1e6:.4f}",
            f"{operable_summary['max_sustainable']['max_operable_harvest_m3'] / 1e6:.4f}",
        ]
        for spec in OPERABLE_SPECS
        for operable_summary in (summary["operable"][spec.key],)
    ]
    operable_table = _md_table(
        [
            "Case",
            "Target Mm3/yr",
            "Annual min",
            "Annual max",
            "Annual mean",
            "Period shortfall Mm3",
            "Province shortfall Mm3",
            "Sustained",
            "Covered sustained",
            "Blocks",
            "Area Mha",
            "Max nominal Mm3/yr",
            "Max operable Mm3/yr",
        ],
        operable_rows,
    )
    osecan = summary["osecan"]
    sustainable_osecan = osecan["sustainable"]
    operable_osecan_summary = osecan["operable"]
    osecan_text = (
        f"Sustainable 100/150: frozen v5.1 {sustainable_osecan['frozen_matches']}/21; "
        + "; ".join(
            f"{key} primary {value['primary_matches']}/7, achieved "
            f"{value['secondary_matches']}/7"
            for key, value in sustainable_osecan["cases"].items()
        )
        + f". Operable 100/119/150: frozen v5.1 "
        f"{operable_osecan_summary['frozen_matches']}/21; "
        + "; ".join(
            f"{key} primary {value['primary_matches']}/7, achieved "
            f"{value['secondary_matches']}/7"
            for key, value in operable_osecan_summary["cases"].items()
        )
        + "."
    )
    report = f"""# Transformed-curve WS4 sustainable and operable cases

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** {summary["status"]}. The 100 and 150 Mm3/yr sustainable cases and
the 100, 119, and 150 Mm3/yr operable cases were rerun on the transformed
national RSYC curves. All outputs are model estimates, not AACs, policy
results, tenure plans, legal THLB determinations, official OSeCAN changes, or
national supply claims.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`transformed_curves=true`, `official_osecan_changed=false`,
`chain_v51_unchanged=true`.

## 1. Dataset

- Dataset: `{summary["dataset"]["path"]}`.
- Curve patch: {summary["dataset"]["mapping"]["ws4_curves_mapped"]} WS4 curves
  mapped to {summary["dataset"]["mapping"]["models_mapped"]} RSYC models from
  {summary["dataset"]["mapping"]["transformed_cell_curves"]} transformed cell
  curves.
- V100 max error: {summary["dataset"]["v100"]["max_abs_error_m3_ha"]:.6f} m3/ha
  (tolerance {summary["dataset"]["v100"]["tolerance_m3_ha"]} m3/ha).
- WS4 validate: `{summary["dataset"]["ws4_validate"].split("→")[0].strip()}`.

## 2. Sustainable 100/150 Mm3/yr

{sustainable_table}

Both cases use the unchanged 100-year whole-block scheduler with the frozen
provincial allocated shares. Period shortfall is the national 10-year target
gap; the annual floor is reported against the nominal annual rate.

## 3. Chain versus frozen v5.1

{chain_table}

The first-period basis applies frozen v5.1 per-province coefficients to the
annual average of offsets 0-9. The achieved basis applies them to the 100-year
annual average. Reference values are the frozen v5.1 100/150 Mm3 targets.

## 4. Operable cases 100/119/150 Mm3/yr

{operable_table}

The operable landbase is the existing `screen_core30` province-share mask:
{summary["operable_screen"]["national"]["selected_blocks"]:,} blocks,
{summary["operable_screen"]["national"]["selected_area_ha"] / 1e6:.3f} Mha.
The mask is an area-share proxy, not a legal THLB, slope screen, or spatial
road overlay. Provincial shortfalls are retained and the cross-province
fallback is disabled.

## 5. OSeCAN handoff

{osecan_text} Official OSeCAN is unchanged in both builds:
`sustainable={sustainable_osecan["official_unchanged"]}`,
`operable={operable_osecan_summary["official_unchanged"]}`.

## 6. Integrity

- Frozen chain v5.1 unchanged: `{summary["frozen_integrity"]["chain_v51_unchanged"]}`.
- Official OSeCAN unchanged: `{summary["frozen_integrity"]["official_osecan_unchanged"]}`.
- Original dataset unchanged: `{summary["frozen_integrity"]["original_dataset_unchanged"]}`.
- Unexpected deletion status: `{summary["d_status"]["no_unexpected_deletions"]}`.

## 7. Caveats

"""
    report += "\n".join(f"- {caveat}" for caveat in CAVEATS) + "\n"
    (CASES_ROOT / REPORT_NAME).write_text(report, encoding="utf-8")


def _write_combined_manifest(summary: Mapping[str, Any]) -> None:
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(CASES_ROOT.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    inputs: dict[str, str] = {}
    for spec in SUSTAINABLE_SPECS + OPERABLE_SPECS:
        inputs[_relative(spec.dataset_dir / "manifest.json")] = _sha256(
            spec.dataset_dir / "manifest.json"
        )
        inputs[_relative(spec.control_csv)] = _sha256(spec.control_csv)
    inputs[_relative(chain.CHAIN_V51_CSV)] = _sha256(chain.CHAIN_V51_CSV)
    inputs[_relative(chain.CHAIN_V51_COEFFICIENTS)] = _sha256(
        chain.CHAIN_V51_COEFFICIENTS
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "title": ARTIFACT_TITLE,
        "labels": summary["labels"],
        "cases": {
            "sustainable": {
                spec.key: {
                    "chain_dir": _relative(spec.chain_dir),
                    "schedule_dir": _relative(spec.output_dir),
                    "target_annual_m3": summary["sustainable"][spec.key]["target"][
                        "annual_m3"
                    ],
                    "sustained": summary["sustainable"][spec.key]["schedule"][
                        "sustained"
                    ],
                }
                for spec in SUSTAINABLE_SPECS
            },
            "operable": {
                spec.key: {
                    "chain_dir": _relative(spec.chain_dir),
                    "schedule_dir": _relative(spec.output_dir),
                    "target_annual_m3": summary["operable"][spec.key]["target"][
                        "annual_m3"
                    ],
                    "sustained": summary["operable"][spec.key]["schedule"][
                        "sustained"
                    ],
                    "max_nominal_control_m3": summary["operable"][spec.key][
                        "max_sustainable"
                    ]["max_nominal_control_m3"],
                }
                for spec in OPERABLE_SPECS
            },
        },
        "operable_mask": {
            "dir": _relative(OPERABLE_MASK_DIR),
            "selected_area_ha": summary["operable_screen"]["national"][
                "selected_area_ha"
            ],
            "selected_blocks": summary["operable_screen"]["national"][
                "selected_blocks"
            ],
        },
        "inputs": inputs,
        "outputs": outputs,
        "osecan": summary["osecan"],
        "validation": summary["validation"],
        "frozen_integrity": summary["frozen_integrity"],
        "d_status": summary["d_status"],
    }
    _write_json(CASES_ROOT / "manifest.json", manifest)


def build() -> dict[str, Any]:
    """Run the transformed sustainable and operable cases end to end."""
    d_before = base.assert_no_unexpected_deletions()
    from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff

    chain_v51_before = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    original_dataset_hashes_before = {
        name: _sha256(ORIGINAL_DATASET / name)
        for name in ("curves.parquet", "blocks.parquet", "fragments.parquet")
    }
    if not TRANSFORMED_119_SUMMARY.is_file():
        raise Ws4TransformedCasesError(
            f"transformed 119 Mm3 full-forest summary is missing: "
            f"{TRANSFORMED_119_SUMMARY}"
        )
    CASES_ROOT.mkdir(parents=True, exist_ok=True)

    dataset_summary = _read_json(TRANSFORMED_ROOT / "build_summary.json")
    if dataset_summary["mapping"]["ws4_curves_mapped"] != 718:
        raise Ws4TransformedCasesError("transformed dataset does not map 718 curves")
    if dataset_summary["v100"]["max_abs_error_m3_ha"] > dataset_summary["v100"][
        "tolerance_m3_ha"
    ]:
        raise Ws4TransformedCasesError("transformed dataset does not preserve V100")

    validate_text = base.run_ws4_validate(DATASET_DIR)
    if "valid" not in validate_text.lower():
        raise Ws4TransformedCasesError("WS4 validate did not report a valid dataset")
    estate = base.load_estate(DATASET_DIR)
    rotation_table = base.compute_rotation_table(
        estate.curve_ids, estate.curve_matrix
    )

    sustainable_summaries = {
        spec.key: cases._run_case(spec, estate) for spec in SUSTAINABLE_SPECS
    }
    for spec in SUSTAINABLE_SPECS:
        _finalize_case(spec, sustainable_summaries[spec.key])

    mask, mask_province_summary, mask_summary = operable.build_operable_mask(
        estate, OPERABLE_MASK_DIR
    )
    _ = mask_province_summary
    _assert_mask_matches_original()
    operable_summaries = {
        ospec.key: operable._run_case(
            ospec,
            estate,
            mask,
            mask_summary,
            rotation_table,
            validate_text,
        )
        for ospec in OPERABLE_SPECS
    }
    for ospec in OPERABLE_SPECS:
        _finalize_case(ospec, operable_summaries[ospec.key])

    sustainable_osecan_summary = sustainable_osecan.build(
        case_specs=SUSTAINABLE_SPECS,
        case_dirs={spec.key: spec.chain_dir for spec in SUSTAINABLE_SPECS},
        output_dir=OSECAN_ROOT / "sustainable",
    )
    sustainable_osecan_summary = _relabel_osecan_tree(OSECAN_ROOT / "sustainable")
    operable_osecan_summary = operable_osecan.build(
        case_specs=OPERABLE_SPECS,
        case_dirs={ospec.key: ospec.chain_dir for ospec in OPERABLE_SPECS},
        output_dir=OSECAN_ROOT / "operable",
    )
    operable_osecan_summary = _relabel_osecan_tree(OSECAN_ROOT / "operable")

    comparison_sustainable = _sustainable_comparison(sustainable_summaries)
    _write_csv(
        CASES_ROOT / "comparison_sustainable_vs_v51.csv",
        [
            "case",
            "metric",
            "unit",
            "chain_v51_value",
            "primary_basis",
            "primary_value",
            "primary_delta",
            "primary_delta_pct",
            "achieved_value",
            "achieved_delta",
            "achieved_delta_pct",
            *ROW_LABELS.keys(),
        ],
        comparison_sustainable,
    )
    comparison_original = _sustainable_vs_original(sustainable_summaries)
    _write_csv(
        CASES_ROOT / "comparison_sustainable_to_original_curves.csv",
        list(comparison_original[0].keys()),
        comparison_original,
    )
    comparison_operable = operable._combined_comparison(operable_summaries)
    for row in comparison_operable:
        row.update(ROW_LABELS)
    _write_csv(
        CASES_ROOT / "comparison_operable_vs_transformed_full_forest.csv",
        list(comparison_operable[0].keys()),
        comparison_operable,
    )
    comparison_operable_original = _operable_vs_original_operable(
        operable_summaries
    )
    _write_csv(
        CASES_ROOT / "comparison_operable_vs_original_curves.csv",
        list(comparison_operable_original[0].keys()),
        comparison_operable_original,
    )
    _write_csv(
        CASES_ROOT / "comparison_operable_chain_vs_v51.csv",
        [
            "case",
            "metric",
            "unit",
            "chain_v51_value",
            "primary_basis",
            "primary_value",
            "primary_delta",
            "primary_delta_pct",
            "achieved_value",
            "achieved_delta",
            "achieved_delta_pct",
            *ROW_LABELS.keys(),
            *OPERABLE_LABELS.keys(),
        ],
        [
            {
                "case": spec.key,
                "metric": row["metric"],
                "unit": row["unit"],
                "chain_v51_value": row["chain_v51_value"],
                "primary_basis": spec.primary_basis,
                "primary_value": row[f"{spec.primary_basis}_value"],
                "primary_delta": row[f"{spec.primary_basis}_delta"],
                "primary_delta_pct": row[f"{spec.primary_basis}_delta_pct"],
                "achieved_value": row[f"{cases.SECONDARY_BASIS}_value"],
                "achieved_delta": row[f"{cases.SECONDARY_BASIS}_delta"],
                "achieved_delta_pct": row[
                    f"{cases.SECONDARY_BASIS}_delta_pct"
                ],
                **ROW_LABELS,
                **OPERABLE_LABELS,
            }
            for spec in OPERABLE_SPECS
            for row in operable_summaries[spec.key]["chain"]["comparison"]
        ],
    )

    original_dataset_hashes_after = {
        name: _sha256(ORIGINAL_DATASET / name)
        for name in ("curves.parquet", "blocks.parquet", "fragments.parquet")
    }
    original_dataset_unchanged = (
        original_dataset_hashes_before == original_dataset_hashes_after
    )
    if not original_dataset_unchanged:
        raise Ws4TransformedCasesError(
            "original national 990 m dataset hashes changed during the build"
        )
    chain_v51_after = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_after = handoff._tree_state(handoff.OSECAN_DIR)
    if chain_v51_before != chain_v51_after:
        raise Ws4TransformedCasesError(
            "frozen chain v5.1 tree changed during the transformed build"
        )
    if osecan_before != osecan_after:
        raise Ws4TransformedCasesError(
            "official OSeCAN tree changed during the transformed build"
        )

    sustainable_compact = {
        spec.key: {
            "target": sustainable_summaries[spec.key]["target"],
            "schedule": sustainable_summaries[spec.key]["schedule"],
            "rotation": sustainable_summaries[spec.key]["rotation"],
            "rotation_mean_years": _rotation_area_weighted_mean(
                spec.output_dir / "selected_blocks.csv"
            ),
            "harvest": sustainable_summaries[spec.key]["harvest"],
            "chain": sustainable_summaries[spec.key]["chain"],
            "ws4": sustainable_summaries[spec.key]["ws4"],
        }
        for spec in SUSTAINABLE_SPECS
    }
    operable_compact = {
        spec.key: {
            "target": operable_summaries[spec.key]["target"],
            "schedule": operable_summaries[spec.key]["schedule"],
            "rotation": operable_summaries[spec.key]["rotation"],
            "harvest": operable_summaries[spec.key]["harvest"],
            "chain": operable_summaries[spec.key]["chain"],
            "max_sustainable": operable_summaries[spec.key]["max_sustainable"],
            "full_forest_reference": operable_summaries[spec.key][
                "full_forest_reference"
            ],
            "ws4": operable_summaries[spec.key]["ws4"],
        }
        for spec in OPERABLE_SPECS
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": (
            "pass"
            if all(
                sustainable_summaries[spec.key]["schedule"]["sustained"]
                for spec in SUSTAINABLE_SPECS
            )
            else "shortfall"
        ),
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "dataset": {
            "path": _relative(DATASET_DIR),
            "build_summary": _relative(TRANSFORMED_ROOT / "build_summary.json"),
            "ws4_validate": validate_text,
            "mapping": dataset_summary["mapping"],
            "v100": dataset_summary["v100"],
        },
        "sustainable": sustainable_compact,
        "operable_screen": mask_summary,
        "operable": operable_compact,
        "comparison_sustainable_vs_v51": comparison_sustainable,
        "comparison_sustainable_to_original_curves": comparison_original,
        "comparison_operable_vs_transformed_full_forest": comparison_operable,
        "comparison_operable_vs_original_curves": comparison_operable_original,
        "osecan": {
            "sustainable": {
                "dir": _relative(OSECAN_ROOT / "sustainable"),
                "status": sustainable_osecan_summary["status"],
                "status_text": sustainable_osecan_summary["status_text"],
                "frozen_matches": sustainable_osecan_summary["frozen_matches"],
                "frozen_21_of_21": sustainable_osecan_summary["frozen_21_of_21"],
                "cases": {
                    key: {
                        "positions": value["positions"],
                        "primary_basis": value["primary_basis"],
                        "primary_matches": value["primary_matches"],
                        "secondary_matches": value["secondary_matches"],
                    }
                    for key, value in sustainable_osecan_summary["cases"].items()
                },
                "official_unchanged": sustainable_osecan_summary[
                    "official_unchanged"
                ],
                "official_detail": sustainable_osecan_summary["official_detail"],
            },
            "operable": {
                "dir": _relative(OSECAN_ROOT / "operable"),
                "status": operable_osecan_summary["status"],
                "status_text": operable_osecan_summary["status_text"],
                "frozen_matches": operable_osecan_summary["frozen_matches"],
                "frozen_21_of_21": operable_osecan_summary["frozen_21_of_21"],
                "cases": {
                    key: {
                        "positions": value["positions"],
                        "primary_basis": value["primary_basis"],
                        "primary_matches": value["primary_matches"],
                        "secondary_matches": value["secondary_matches"],
                    }
                    for key, value in operable_osecan_summary["cases"].items()
                },
                "official_unchanged": operable_osecan_summary["official_unchanged"],
                "official_detail": operable_osecan_summary["official_detail"],
            },
        },
        "validation": {
            "sustainable_case_count": len(SUSTAINABLE_SPECS),
            "operable_case_count": len(OPERABLE_SPECS),
            "sustainable_all_sustained": all(
                sustainable_summaries[spec.key]["schedule"]["sustained"]
                for spec in SUSTAINABLE_SPECS
            ),
            "operable_sustained_cases": [
                spec.key
                for spec in OPERABLE_SPECS
                if operable_summaries[spec.key]["schedule"]["sustained"]
            ],
            "operable_sustained_covered_cases": [
                spec.key
                for spec in OPERABLE_SPECS
                if operable_summaries[spec.key]["schedule"][
                    "sustained_covered_provinces"
                ]
            ],
            "no_under_rotation": all(
                bool(
                    (
                        pd.read_csv(spec.output_dir / "selected_blocks.csv")[
                            "age_at_harvest"
                        ]
                        >= pd.read_csv(spec.output_dir / "selected_blocks.csv")[
                            "rotation_years"
                        ]
                        - 1e-9
                    ).all()
                )
                for spec in SUSTAINABLE_SPECS + OPERABLE_SPECS
            ),
            "mask_matches_original": True,
            "osecan_checks": {
                "sustainable": len(
                    sustainable_osecan_summary["validation"]["checks"]
                ),
                "operable": len(operable_osecan_summary["validation"]["checks"]),
            },
        },
        "frozen_integrity": {
            "chain_v51_before": chain_v51_before,
            "chain_v51_after": chain_v51_after,
            "chain_v51_unchanged": chain_v51_before == chain_v51_after,
            "official_osecan_before": osecan_before,
            "official_osecan_after": osecan_after,
            "official_osecan_unchanged": osecan_before == osecan_after,
            "original_dataset_hashes": original_dataset_hashes_after,
            "original_dataset_unchanged": original_dataset_unchanged,
        },
        "d_status": base.assert_no_unexpected_deletions(),
    }
    if d_before["no_unexpected_deletions"] is not True:
        raise Ws4TransformedCasesError("unexpected deletion status before the build")
    _write_json(CASES_ROOT / "summary.json", summary)
    _write_combined_report(summary)
    _write_combined_manifest(summary)
    return summary


def main() -> None:
    print(json.dumps(build(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
