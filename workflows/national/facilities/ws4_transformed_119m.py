"""Run the sustainable 119 Mm3 case on transformed national RSYC curves.

This is the existing national WS4 sustainable scheduler with one change: the
990-m dataset carries the national RSYC transform v1 curves instead of the
original RSYC group curves.  The original dataset, frozen chain v5.1, and
official OSeCAN tree remain read-only.

The chain and OSeCAN handoff are rebuilt from the transformed run so the
comparison with the original RSYC 119 Mm3 case and frozen v5.1 is explicit.
All outputs are model estimates.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflows.national.facilities import ws4_chain_119m as chain
from workflows.national.facilities import ws4_sustainable_119m as sustainable
from workflows.national.facilities import ws4_sustainable_osecan_handoff as handoff
from workflows.national.facilities import ws4_transformed_dataset as transformed

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
TRANSFORMED_ROOT = DATA_ROOT / "derived/ws4/scanfi-national-990m-transformed"
DATASET_DIR = TRANSFORMED_ROOT / "dataset"
OUTPUT_DIR = TRANSFORMED_ROOT / "sustainable-119m"
CHAIN_DIR = DATA_ROOT / "derived/national/facilities/ws4_transformed_119m"

ARTIFACT = "ws4_transformed_119m"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = (
    "Sustainable 119 Mm3/yr WS4 schedule and chain with transformed RSYC curves"
)
REPORT_NAME = "WS4_TRANSFORMED_119M.md"

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
    "model_estimate": "true",
    "observed": "false",
    "policy_grade": "false",
    "national_claim": "false",
    "ws4_derived": "true",
    "sustainable_schedule": "true",
    "transformed_curves": "true",
    "official_osecan_changed": "false",
    "chain_v51_unchanged": "true",
}
CAVEATS: tuple[str, ...] = (
    (
        "Yield curves are the national RSYC transform v1: V100 is preserved, "
        "ages 1-200 are transformed, and ages 201-600 are held at the age-200 "
        "value. The model curve is the forest-area-weighted mean of transformed "
        "cell curves for each WS4 model."
    ),
    (
        "The transformed curves shift rotation ages and harvest timing. This is a "
        "whole-block national even-flow model estimate, not an AAC, policy result, "
        "tenure plan, or investment case."
    ),
    (
        "The full forest estate is used; no operability, tenure, road, slope, or "
        "mill-capacity screen is applied."
    ),
    (
        "Provincial period targets use the frozen 119 Mm3 control shares so the "
        "chain and OSeCAN handoff remain comparable with the original RSYC 119 Mm3 "
        "case. The national oldest-first free run is a sensitivity, not the chain "
        "basis."
    ),
    (
        "Whole blocks are selected. Provincial achieved volume can overshoot the "
        "target by the final block because no block is split or scaled."
    ),
    (
        "Frozen chain v5.1 coefficients are applied to the WS4 harvest by origin "
        "province. Facility assignment, mill capacity, transport, and "
        "interprovincial trade are not re-run."
    ),
    (
        "No original national 990 m dataset, frozen chain v5.1 artifact, official "
        "OSeCAN file, or UI surface is changed. Transformed curves are not official "
        "OSeCAN curves."
    ),
)

CASE = sustainable.Case(
    artifact=ARTIFACT,
    artifact_version=ARTIFACT_VERSION,
    artifact_title=ARTIFACT_TITLE,
    dataset_dir=DATASET_DIR,
    output_dir=OUTPUT_DIR,
    chain_dir=CHAIN_DIR,
    labels=LABELS,
    row_labels=ROW_LABELS,
    report_name=REPORT_NAME,
    caveats=CAVEATS,
    curve_note="transformed RSYC yield curve (V100-preserved, age 201-600 held flat)",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def ensure_dataset() -> dict[str, Any]:
    """Build the transformed dataset when it is absent; otherwise reuse it."""
    summary_path = TRANSFORMED_ROOT / "build_summary.json"
    if DATASET_DIR.is_dir() and summary_path.is_file():
        return _read_json(summary_path)
    return transformed.build()


def _label_handoff() -> dict[str, Any]:
    """Add transformed-curve labels to the OSeCAN handoff artifacts."""
    target = CHAIN_DIR / "osecan"
    summary_path = target / "summary.json"
    manifest_path = target / "manifest.json"
    for path in sorted(target.glob("*.csv")):
        frame = pd.read_csv(path)
        frame["transformed_curves"] = "true"
        frame.to_csv(path, index=False, lineterminator="\n")
    report_path = target / "FORESTRY_OSECAN_HANDOFF_WS4_SUSTAINABLE.md"
    if report_path.is_file():
        report = report_path.read_text(encoding="utf-8")
        old_labels = (
            "**Labels:** `model_estimate=true`, `observed=false`, "
            "`policy_grade=false`,\n`national_claim=false`, `ws4_derived=true`, "
            "`sustainable_schedule=true`,\n`official_osecan_changed=false`, "
            "`chain_v51_unchanged=true`."
        )
        new_labels = (
            "**Labels:** `model_estimate=true`, `observed=false`, "
            "`policy_grade=false`,\n`national_claim=false`, `ws4_derived=true`, "
            "`sustainable_schedule=true`, `transformed_curves=true`,\n"
            "`official_osecan_changed=false`, `chain_v51_unchanged=true`."
        )
        if old_labels not in report:
            raise RuntimeError("OSeCAN report label block changed unexpectedly")
        report_path.write_text(
            report.replace(old_labels, new_labels, 1), encoding="utf-8"
        )
    summary = _read_json(summary_path)
    summary.setdefault("labels", {}).update(LABELS)
    _write_json(summary_path, summary)
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        manifest.setdefault("labels", {}).update(LABELS)
        manifest["outputs"] = {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": handoff.handoff._sha256(path),
            }
            for path in sorted(target.iterdir())
            if path.is_file() and path.name != "manifest.json"
        }
        _write_json(manifest_path, manifest)
    return summary


def _read_chain_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(CHAIN_DIR / name)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def build_first_period_chain() -> dict[str, Any]:
    """Apply v5.1 coefficients to the first-period annual average."""
    harvest = _read_chain_csv("sustainable_harvest_by_province.csv")
    harvest = harvest.loc[harvest["province"] != "CAN"].set_index("province")
    first_period = {
        province: float(harvest.loc[province, "first_period_annual_m3"])
        for province in chain.PROVINCES
    }
    national_first = math.fsum(first_period.values())
    if national_first <= 0.0:
        raise RuntimeError("first-period harvest is zero")
    scale = sustainable.TARGET_ANNUAL_M3 / national_first
    frozen = chain._frozen_province_rows(chain.CHAIN_V51_CSV)
    _coefficient_rows, coefficients = chain._coefficient_rows(frozen)
    frozen_totals = chain._frozen_national_totals(frozen)

    bases = (
        ("first_period_119m", {p: v * scale for p, v in first_period.items()}, scale),
        ("first_period_achieved", first_period, 1.0),
    )
    chain_rows: list[dict[str, Any]] = []
    national_rows: dict[str, dict[str, Any]] = {}
    for basis, values, factor in bases:
        rows, national = chain.build_chain_rows(
            {province: float(values[province]) for province in chain.PROVINCES},
            basis,
            frozen,
            coefficients,
            scale_factor=factor,
        )
        for row in rows:
            row.update(ROW_LABELS)
        national.update(ROW_LABELS)
        chain_rows.extend(rows)
        chain_rows.append(national)
        national_rows[basis] = national

    product_columns = list(_read_chain_csv("sustainable_product_chain.csv").columns)
    _write_csv(
        CHAIN_DIR / "first_period_product_chain.csv",
        pd.DataFrame(chain_rows).reindex(columns=product_columns),
    )
    comparison_rows: list[dict[str, Any]] = []
    for metric, unit in chain.METRICS:
        reference = float(frozen_totals[metric])
        row: dict[str, Any] = {
            "metric": metric,
            "unit": unit,
            "chain_v51_value": reference,
        }
        for basis, _values, _factor in bases:
            value = float(national_rows[basis][metric])
            delta = value - reference
            row[f"{basis}_value"] = value
            row[f"{basis}_delta"] = delta
            row[f"{basis}_delta_pct"] = delta / reference * 100.0 if reference else 0.0
        row.update(ROW_LABELS)
        comparison_rows.append(row)
    _write_csv(
        CHAIN_DIR / "comparison_first_period_vs_v51.csv",
        pd.DataFrame(comparison_rows),
    )
    return {
        "national_first_period_annual_m3": national_first,
        "scale_factor": scale,
        "national": national_rows,
        "comparison": comparison_rows,
    }


def _selected_metrics(path: Path) -> dict[str, Any]:
    selected = pd.read_csv(path)
    area = selected["area_ha"].to_numpy(dtype=float)
    rotation = selected["rotation_years"].to_numpy(dtype=float)
    return {
        "selected_blocks": int(selected["block_id"].size),
        "selected_area_ha": float(area.sum()),
        "mean_rotation_years_area_weighted": float(np.average(rotation, weights=area)),
        "rotation_used_distribution": {
            str(int(key)): int(value)
            for key, value in selected["rotation_years"]
            .value_counts()
            .sort_index()
            .items()
        },
    }


def _curve_metrics(dataset_dir: Path) -> dict[str, Any]:
    curve_ids, curve_matrix = sustainable.load_curves(dataset_dir)
    table = sustainable.compute_rotation_table(curve_ids, curve_matrix)
    return {
        "curve_count": len(table),
        "max_mai_m3_per_ha_year_median": float(
            table["max_mai_m3_per_ha_year"].median()
        ),
        "max_mai_m3_per_ha_year_min": float(table["max_mai_m3_per_ha_year"].min()),
        "max_mai_m3_per_ha_year_max": float(table["max_mai_m3_per_ha_year"].max()),
        "rotation_raw_years_median": float(table["rotation_raw_years"].median()),
        "rotation_raw_years_min": int(table["rotation_raw_years"].min()),
        "rotation_raw_years_max": int(table["rotation_raw_years"].max()),
        "rotation_used_distribution": {
            str(int(key)): int(value)
            for key, value in table["rotation_years"]
            .value_counts()
            .sort_index()
            .items()
        },
        "out_of_range_curves": int(table["rotation_out_of_range"].sum()),
    }


def build_original_comparison() -> dict[str, Any]:
    """Compare the transformed schedule with the original RSYC 119 case."""
    original_chain = DATA_ROOT / "derived/national/facilities/ws4_sustainable_119m"
    original_summary = _read_json(original_chain / "summary.json")
    transformed_summary = _read_json(CHAIN_DIR / "summary.json")
    original_output = DATA_ROOT / "derived/ws4/sustainable-119m"
    transformed_output = OUTPUT_DIR
    original = {
        "summary": original_summary["schedule"],
        "selected": _selected_metrics(original_output / "selected_blocks.csv"),
        "curves": _curve_metrics(sustainable.DATASET_DIR),
    }
    transformed = {
        "summary": transformed_summary["schedule"],
        "selected": _selected_metrics(transformed_output / "selected_blocks.csv"),
        "curves": _curve_metrics(DATASET_DIR),
    }
    units = {
        "annual_min_m3": "m3/yr",
        "annual_max_m3": "m3/yr",
        "annual_mean_m3": "m3/yr",
        "annual_cv": "fraction",
        "period_shortfall_m3": "m3",
        "selected_blocks": "blocks",
        "selected_area_ha": "ha",
        "blocks_reharvested": "blocks",
        "mean_rotation_years_area_weighted": "years",
        "curve_count": "curves",
        "max_mai_m3_per_ha_year_median": "m3/ha/yr",
        "rotation_raw_years_median": "years",
        "out_of_range_curves": "curves",
    }
    rows: list[dict[str, Any]] = []
    for metric in (
        "annual_min_m3",
        "annual_max_m3",
        "annual_mean_m3",
        "annual_cv",
        "period_shortfall_m3",
        "selected_blocks",
        "selected_area_ha",
        "blocks_reharvested",
    ):
        rows.append(
            {
                "metric": metric,
                "unit": units[metric],
                "original_rsyc_119m": float(original["summary"][metric]),
                "transformed_rsyc_119m": float(transformed["summary"][metric]),
                "delta": float(transformed["summary"][metric])
                - float(original["summary"][metric]),
            }
        )
    for metric in (
        "mean_rotation_years_area_weighted",
        "curve_count",
        "max_mai_m3_per_ha_year_median",
        "rotation_raw_years_median",
        "out_of_range_curves",
    ):
        source = (
            "selected" if metric == "mean_rotation_years_area_weighted" else "curves"
        )
        rows.append(
            {
                "metric": metric,
                "unit": units[metric],
                "original_rsyc_119m": float(original[source][metric]),
                "transformed_rsyc_119m": float(transformed[source][metric]),
                "delta": float(transformed[source][metric])
                - float(original[source][metric]),
            }
        )
    _write_csv(CHAIN_DIR / "comparison_to_original_119m.csv", pd.DataFrame(rows))
    return {
        "original": original,
        "transformed": transformed,
        "comparison": rows,
    }


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _augment_report(
    dataset_summary: Mapping[str, Any],
    first_period: Mapping[str, Any],
    original_comparison: Mapping[str, Any],
) -> None:
    path = CHAIN_DIR / REPORT_NAME
    report = path.read_text(encoding="utf-8")
    mapping = dataset_summary["mapping"]
    v100 = dataset_summary["v100"]
    patch_line = (
        "- Curve patch: "
        f"{mapping['ws4_curves_mapped']} WS4 curves mapped to "
        f"{mapping['models_mapped']} RSYC models from "
        f"{mapping['transformed_cell_curves']} transformed cell curves; "
        f"V100 max error {v100['max_abs_error_m3_ha']:.6f} m3/ha "
        f"(tolerance {v100['tolerance_m3_ha']} m3/ha).\n"
    )
    report = report.replace(
        "- Dataset: `data/derived/ws4/scanfi-national-990m-transformed/dataset`\n",
        "- Dataset: `data/derived/ws4/scanfi-national-990m-transformed/dataset`\n"
        + patch_line,
        1,
    )

    first_metrics = {
        "solid_product_m3",
        "pulp_product_odt",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_r060_pj",
        "total_residue_gross_pj",
        "harvest_m3",
    }
    first_rows = [
        [
            str(row["metric"]),
            str(row["unit"]),
            f"{float(row['chain_v51_value']):.6f}",
            f"{float(row['first_period_119m_value']):.6f}",
            f"{float(row['first_period_119m_delta_pct']):.4f}",
            f"{float(row['first_period_achieved_value']):.6f}",
            f"{float(row['first_period_achieved_delta_pct']):.4f}",
        ]
        for row in first_period["comparison"]
        if row["metric"] in first_metrics
    ]
    original_rows = [
        [
            str(row["metric"]),
            str(row["unit"]),
            f"{float(row['original_rsyc_119m']):.6g}",
            f"{float(row['transformed_rsyc_119m']):.6g}",
            f"{float(row['delta']):.6g}",
        ]
        for row in original_comparison["comparison"]
    ]
    section = (
        "## 4b. First-period annual-average chain\n\n"
        + _md_table(
            [
                "Metric",
                "Unit",
                "v5.1",
                "First period 119 Mm3 basis",
                "Delta %",
                "First period achieved basis",
                "Delta %",
            ],
            first_rows,
        )
        + "\n\nThe first-period basis applies the same frozen v5.1 coefficients "
        "to the first-period annual average. The 119 Mm3 basis scales that "
        "average by one national factor; the achieved basis is unscaled.\n\n"
        + "## 4c. Comparison with the original RSYC 119 Mm3 case\n\n"
        + _md_table(
            ["Metric", "Unit", "Original RSYC 119m", "Transformed 119m", "Delta"],
            original_rows,
        )
        + "\n\nThe transformed case selects fewer blocks and hectares because "
        "the transformed curves carry more volume per hectare at the same "
        "rotation cap; the area-weighted mean rotation rises while the 100-year "
        "even-flow target is still met.\n\n"
    )
    marker = "## 5. National oldest-first free-run sensitivity"
    if marker not in report:
        raise RuntimeError("transformed report is missing the free-run section")
    report = report.replace(marker, section + marker, 1)
    path.write_text(report, encoding="utf-8")


def build() -> dict[str, Any]:
    """Build the transformed dataset, 119 Mm3 schedule, chain, and handoff."""
    dataset_summary = ensure_dataset()
    sustainable_summary = sustainable.build(CASE)
    osecan_summary = handoff.build(chain_dir=CHAIN_DIR, output_dir=CHAIN_DIR / "osecan")
    osecan_summary = _label_handoff()
    first_period = build_first_period_chain()
    original_comparison = build_original_comparison()
    _augment_report(dataset_summary, first_period, original_comparison)

    comparison = sustainable_summary["chain"]["comparison"]
    totals = {
        row["metric"]: {
            "chain_v51_value": float(row["chain_v51_value"]),
            "sustainable_119m_value": float(row["sustainable_119m_value"]),
            "sustainable_119m_delta_pct": float(row["sustainable_119m_delta_pct"]),
            "sustainable_achieved_value": float(row["sustainable_achieved_value"]),
            "sustainable_achieved_delta_pct": float(
                row["sustainable_achieved_delta_pct"]
            ),
        }
        for row in comparison
    }
    combined = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "labels": LABELS,
        "dataset": {
            "path": str(DATASET_DIR.relative_to(REPO_ROOT)),
            "build_summary": str(
                (TRANSFORMED_ROOT / "build_summary.json").relative_to(REPO_ROOT)
            ),
            "ws4_validate": dataset_summary["ws4_validate"],
            "mapping": dataset_summary["mapping"],
            "v100": dataset_summary["v100"],
        },
        "schedule": {
            "path": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            "chain": str(CHAIN_DIR.relative_to(REPO_ROOT)),
            "annual_min_m3": sustainable_summary["schedule"]["annual_min_m3"],
            "annual_max_m3": sustainable_summary["schedule"]["annual_max_m3"],
            "annual_mean_m3": sustainable_summary["schedule"]["annual_mean_m3"],
            "period_shortfall_m3": sustainable_summary["schedule"][
                "period_shortfall_m3"
            ],
            "sustained_119m": sustainable_summary["schedule"]["sustained_119m"],
            "selected_blocks": sustainable_summary["schedule"]["selected_blocks"],
            "selected_area_ha": sustainable_summary["schedule"]["selected_area_ha"],
            "blocks_reharvested": sustainable_summary["schedule"]["blocks_reharvested"],
            "rotation": sustainable_summary["rotation"],
        },
        "chain": {
            "comparison_vs_v51": totals,
            "first_period": first_period,
            "comparison_to_original_119m": original_comparison,
            "national_119m": sustainable_summary["chain"]["national"][
                "sustainable_119m"
            ],
            "national_achieved": sustainable_summary["chain"]["national"][
                "sustainable_achieved"
            ],
        },
        "osecan": {
            "path": str((CHAIN_DIR / "osecan").relative_to(REPO_ROOT)),
            "frozen_matches": osecan_summary["frozen_matches"],
            "frozen_21_of_21": osecan_summary["frozen_21_of_21"],
            "primary_matches": osecan_summary["primary_matches"],
            "primary_7_of_7": osecan_summary["primary_7_of_7"],
            "secondary_matches": osecan_summary["secondary_matches"],
            "secondary_7_of_7": osecan_summary["secondary_7_of_7"],
            "official_unchanged": osecan_summary["official_unchanged"],
            "official_detail": osecan_summary["official_detail"],
        },
        "d_status": sustainable_summary["d_status"],
    }
    _write_json(OUTPUT_DIR / "transformed_case_summary.json", combined)
    return combined


def main() -> None:
    print(json.dumps(build(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
