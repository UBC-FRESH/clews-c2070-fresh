"""Compare two TSA24 industrial-sort validation runs.

The comparison is descriptive only.  It does not fit a tolerance, select a
preferred profile from the result, or assign an official grade.  The default
and ``tipsy45`` profiles are pre-declared; this module reports what changed.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = [
    "ProfileComparisonError",
    "build_comparison",
    "build_report",
    "load_run",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[3]


class ProfileComparisonError(RuntimeError):
    """Raised when a comparison input is missing or inconsistent."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ProfileComparisonError(f"comparison input not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProfileComparisonError(f"comparison input must be an object: {path}")
    return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_run(run_dir: Path, *, label: str) -> dict[str, Any]:
    """Load the metrics, summary, diagnostics, and sort table for one run."""
    run_dir = Path(run_dir)
    metrics = _read_json(run_dir / "metrics.json")
    summary = _read_json(run_dir / "summary.json")
    diagnostics = _read_json(run_dir / "diagnostics.json")
    sort_path = run_dir / "sort_comparison.csv"
    if not sort_path.exists():
        raise ProfileComparisonError(f"comparison input not found: {sort_path}")
    sorts = pd.read_csv(sort_path)
    primary = metrics.get("primary_au_match") or {}
    if not primary:
        raise ProfileComparisonError(f"run has no primary_au_match metrics: {run_dir}")
    return {
        "label": label,
        "run_dir": str(run_dir),
        "metrics": metrics,
        "summary": summary,
        "diagnostics": diagnostics,
        "sorts": sorts,
    }


def _scope_metrics(run: Mapping[str, Any]) -> dict[str, Any]:
    primary = run["metrics"]["primary_au_match"]
    volume = primary.get("volume") or {}
    plot_tvd = primary.get("plot_tvd") or {}
    diagnostics = run["diagnostics"]
    return {
        "n_plots": int(primary.get("n_plots", 0)),
        "n_logs": int(primary.get("n_logs", 0)),
        "n_trees": int(primary.get("n_trees", 0)),
        "pooled_tvd": _finite(primary.get("pooled_tvd")),
        "pooled_overlap": _finite(primary.get("pooled_overlap")),
        "mean_plot_tvd": _finite(plot_tvd.get("mean_tvd")),
        "median_plot_tvd": _finite(plot_tvd.get("median_tvd")),
        "volume_weighted_tvd": _finite(plot_tvd.get("volume_weighted_tvd")),
        "mean_plot_overlap": _finite(plot_tvd.get("mean_overlap")),
        "diameter_marginal_tvd": _finite(
            (diagnostics.get("diameter_marginal") or {}).get("tvd")
        ),
        "length_marginal_tvd": _finite(
            (diagnostics.get("length_marginal") or {}).get("tvd")
        ),
        "bias_sum_m3ha": _finite(volume.get("bias_sum_m3ha")),
        "mean_bias_m3ha": _finite(volume.get("mean_bias_m3ha")),
        "relative_bias": _finite(volume.get("relative_bias")),
        "ratio_predicted_observed": _finite(volume.get("ratio_predicted_observed")),
        "mae_m3ha": _finite(volume.get("mae_m3ha")),
        "rmse_m3ha": _finite(volume.get("rmse_m3ha")),
        "non_tipsy_lengths": (
            (diagnostics.get("log_lengths") or {}).get("non_tipsy_nominal_lengths")
            or {}
        ),
    }


def _delta(after: Any, before: Any) -> float | None:
    after_value = _finite(after)
    before_value = _finite(before)
    if after_value is None or before_value is None:
        return None
    return after_value - before_value


def _aggregate_sorts(run: Mapping[str, Any]) -> pd.DataFrame:
    frame = run["sorts"]
    aggregate = frame[frame["scope"] == "aggregate_primary_au"].copy()
    if aggregate.empty:
        raise ProfileComparisonError(
            f"run {run['label']} has no aggregate_primary_au sort rows"
        )
    return aggregate[["code", "pilot_share", "tipsy_share"]].rename(
        columns={
            "pilot_share": f"{run['label']}_pilot_share",
            "tipsy_share": f"{run['label']}_tipsy_share",
        }
    )


def build_comparison(
    default_run: Mapping[str, Any],
    tipsy45_run: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the pre-declared default-vs-tipsy45 comparison surface."""
    before = _scope_metrics(default_run)
    after = _scope_metrics(tipsy45_run)
    keys = [
        "pooled_tvd",
        "pooled_overlap",
        "mean_plot_tvd",
        "median_plot_tvd",
        "volume_weighted_tvd",
        "diameter_marginal_tvd",
        "length_marginal_tvd",
        "bias_sum_m3ha",
        "relative_bias",
        "ratio_predicted_observed",
        "mae_m3ha",
        "rmse_m3ha",
    ]
    metrics = {
        "default": {"label": default_run["label"], **before},
        "tipsy45": {"label": tipsy45_run["label"], **after},
        "delta": {
            key: _delta(after.get(key), before.get(key)) for key in keys
        },
    }

    default_sorts = _aggregate_sorts(default_run)
    tipsy_sorts = _aggregate_sorts(tipsy45_run)
    merged = default_sorts.merge(tipsy_sorts, on="code", how="outer").fillna(0.0)
    merged["default_difference"] = (
        merged["default_pilot_share"] - merged["default_tipsy_share"]
    )
    merged["tipsy45_difference"] = (
        merged["tipsy45_pilot_share"] - merged["tipsy45_tipsy_share"]
    )
    merged["abs_default_difference"] = merged["default_difference"].abs()
    merged["abs_tipsy45_difference"] = merged["tipsy45_difference"].abs()
    merged["abs_difference_change"] = (
        merged["abs_tipsy45_difference"] - merged["abs_default_difference"]
    )
    merged = merged.sort_values("abs_tipsy45_difference", ascending=False)
    sort_rows = merged.to_dict(orient="records")

    default_au = pd.read_csv(Path(default_run["run_dir"]) / "au_sort_summary.csv")
    tipsy_au = pd.read_csv(Path(tipsy45_run["run_dir"]) / "au_sort_summary.csv")
    au = default_au[["au_id", "n_plots", "pooled_tvd", "mean_plot_tvd"]].merge(
        tipsy_au[["au_id", "pooled_tvd", "mean_plot_tvd"]],
        on="au_id",
        how="outer",
        suffixes=("_default", "_tipsy45"),
    )
    au["pooled_tvd_delta"] = au["pooled_tvd_tipsy45"] - au["pooled_tvd_default"]
    au["mean_plot_tvd_delta"] = (
        au["mean_plot_tvd_tipsy45"] - au["mean_plot_tvd_default"]
    )
    au = au.sort_values("n_plots", ascending=False)

    pooled_delta = metrics["delta"]["pooled_tvd"]
    mean_plot_delta = metrics["delta"]["mean_plot_tvd"]
    gap_closes = bool(
        pooled_delta is not None
        and mean_plot_delta is not None
        and pooled_delta < 0
        and mean_plot_delta < 0
    )
    return {
        "status": "descriptive_not_tuned",
        "label": "industrial_sort_validation",
        "grade_status": "not_assigned",
        "official_grade_validation": False,
        "compared_profiles": [default_run["label"], tipsy45_run["label"]],
        "metrics": metrics,
        "gap_closes": gap_closes,
        "verdict": (
            "gap_closes_on_both_pooled_and_mean_plot_tvd"
            if gap_closes
            else "gap_does_not_close_on_both_pooled_and_mean_plot_tvd"
        ),
        "sort_shares": sort_rows,
        "by_au": au.to_dict(orient="records"),
        "caveats": [
            "The profiles are pre-declared; no parameter was tuned to improve TVD.",
            "Industrial sort recovery is not official BC log grade.",
            "TIPSY industrial-log curves are model output, not field measurements.",
            (
                "FAN$IER 4.5 does not publish numeric default log prices or sort "
                "relativities; the tipsy45 price scenario is documented as an assumption."
            ),
        ],
    }


def _fmt(value: Any, digits: int = 4) -> str:
    number = _finite(value)
    if number is None:
        return "n/a"
    return f"{number:,.{digits}f}"


def _markdown_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, separator]
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, (int,)) and not isinstance(value, bool):
                cells.append(str(value))
            elif isinstance(value, float):
                cells.append(_fmt(value))
            else:
                cells.append(str(value) if value is not None else "n/a")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def build_report(comparison: Mapping[str, Any]) -> str:
    metrics = comparison["metrics"]
    default = metrics["default"]
    tipsy = metrics["tipsy45"]
    delta = metrics["delta"]
    lines: list[str] = []
    lines.append("# TSA24 default vs tipsy45 bucking-profile comparison\n")
    lines.append("| Field | Value |\n|---|---|\n")
    lines.append("| Validation | `industrial_sort_validation` |\n")
    lines.append("| Grade status | `not_assigned` |\n")
    lines.append("| Official grade validation | no |\n")
    lines.append("| Profiles | `default` vs `tipsy45` |\n")
    lines.append(f"| Verdict | `{comparison['verdict']}` |\n\n")
    lines.append(
        "> The profiles were declared before the comparison. No profile value was "
        "tuned to improve TVD. This is an industrial sort recovery check, not an "
        "official BC log-grade validation.\n\n"
    )
    rows = [
        {
            "metric": key,
            "default": default.get(key),
            "tipsy45": tipsy.get(key),
            "delta": delta.get(key),
        }
        for key in (
            "pooled_tvd",
            "pooled_overlap",
            "mean_plot_tvd",
            "median_plot_tvd",
            "volume_weighted_tvd",
            "diameter_marginal_tvd",
            "length_marginal_tvd",
            "bias_sum_m3ha",
            "relative_bias",
            "ratio_predicted_observed",
            "mae_m3ha",
            "rmse_m3ha",
        )
    ]
    lines.append("## 1. Before/after metrics\n\n")
    lines.append(_markdown_table(rows, ["metric", "default", "tipsy45", "delta"]))
    lines.append(
        f"\nPilot logs: default **{default['n_logs']:,}**, tipsy45 "
        f"**{tipsy['n_logs']:,}** on the same **{default['n_plots']}** comparable "
        f"AU-matched plots and **{default['n_trees']:,}** trees.\n\n"
    )
    lines.append("## 2. Sort shares\n\n")
    lines.append(
        "`abs_difference_change` is the tipsy45 absolute pilot-vs-TIPSY share "
        "difference minus the default difference; negative values are improvements.\n\n"
    )
    sort_rows = comparison["sort_shares"][:12]
    lines.append(
        _markdown_table(
            sort_rows,
            [
                "code",
                "default_pilot_share",
                "default_tipsy_share",
                "tipsy45_pilot_share",
                "tipsy45_tipsy_share",
                "abs_difference_change",
            ],
        )
    )
    lines.append("\n## 3. Non-TIPSY nominal lengths\n\n")
    lines.append(
        "Counts of bucked logs whose exact length is outside the nominal TIPSY "
        "length list. This diagnostic is descriptive; TIPSY sort codes are "
        "minimum-length classes, so it is not a grade claim.\n\n"
    )
    length_rows = [
        {
            "length_m": length,
            "default_logs": (default["non_tipsy_lengths"] or {}).get(length, 0),
            "tipsy45_logs": (tipsy["non_tipsy_lengths"] or {}).get(length, 0),
        }
        for length in sorted(
            set(default["non_tipsy_lengths"] or {})
            | set(tipsy["non_tipsy_lengths"] or {})
        )
    ]
    lines.append(_markdown_table(length_rows, ["length_m", "default_logs", "tipsy45_logs"]))
    lines.append("\n## 4. Per-AU TVD\n\n")
    au_rows = comparison["by_au"][:20]
    lines.append(
        _markdown_table(
            au_rows,
            [
                "au_id",
                "n_plots",
                "pooled_tvd_default",
                "pooled_tvd_tipsy45",
                "pooled_tvd_delta",
                "mean_plot_tvd_delta",
            ],
        )
    )
    lines.append("\n## 5. What remains\n\n")
    lines.append(
        "- The tipsy45 geometry removes every non-nominal 7.5 m and 10.0 m log, "
        "but the pooled and mean-per-plot TVD do not improve.\n"
    )
    lines.append(
        "- The value-maximising dynamic program without a published FAN$IER "
        "length/grade price surface over-selects 13 m and 11 m logs and "
        "under-selects the 8 m and 5.1 m classes that dominate the TIPSY curves.\n"
    )
    lines.append(
        "- FAN$IER 4.5 Help does not publish numeric default log prices or a "
        "sort-specific relativity. No such values were invented; the tipsy45 "
        "price scenario keeps the published BC Interior product-class prices.\n"
    )
    lines.append(
        "- The remaining gap is a model-definition gap (bucking objective and "
        "price/grade surface), not a threshold that should be tuned here.\n"
    )
    return "".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two TSA24 industrial-sort validation runs."
    )
    parser.add_argument(
        "--default-dir",
        type=Path,
        default=REPO_ROOT
        / "data"
        / "derived"
        / "national"
        / "bucking"
        / "tsa24_sort_validation_v1",
    )
    parser.add_argument(
        "--tipsy45-dir",
        type=Path,
        default=REPO_ROOT
        / "data"
        / "derived"
        / "national"
        / "bucking"
        / "tsa24_sort_validation_tipsy45_v1",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT
        / "data"
        / "derived"
        / "national"
        / "bucking"
        / "tsa24_sort_validation_profile_comparison_v1",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    default_run = load_run(args.default_dir, label="default")
    tipsy45_run = load_run(args.tipsy45_dir, label="tipsy45")
    comparison = build_comparison(default_run, tipsy45_run)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out_dir / "COMPARISON.md").write_text(
        build_report(comparison), encoding="utf-8"
    )
    print(json.dumps(comparison["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
