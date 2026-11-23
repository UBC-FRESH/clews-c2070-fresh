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


METRIC_KEYS = [
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


def _gap_closes(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    pooled_delta = _delta(after.get("pooled_tvd"), before.get("pooled_tvd"))
    mean_plot_delta = _delta(after.get("mean_plot_tvd"), before.get("mean_plot_tvd"))
    return bool(
        pooled_delta is not None
        and mean_plot_delta is not None
        and pooled_delta < 0
        and mean_plot_delta < 0
    )


def _merge_sort_shares(
    default_run: Mapping[str, Any],
    tipsy45_run: Mapping[str, Any],
    market_run: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    merged = _aggregate_sorts(default_run).merge(
        _aggregate_sorts(tipsy45_run), on="code", how="outer"
    )
    if market_run is not None:
        merged = merged.merge(_aggregate_sorts(market_run), on="code", how="outer")
    merged = merged.fillna(0.0)
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
    if market_run is not None:
        merged["market_surface_difference"] = (
            merged["market_surface_pilot_share"] - merged["market_surface_tipsy_share"]
        )
        merged["abs_market_surface_difference"] = merged[
            "market_surface_difference"
        ].abs()
        merged["abs_market_vs_default_change"] = (
            merged["abs_market_surface_difference"] - merged["abs_default_difference"]
        )
        merged["abs_market_vs_tipsy45_change"] = (
            merged["abs_market_surface_difference"] - merged["abs_tipsy45_difference"]
        )
        merged = merged.sort_values("abs_market_surface_difference", ascending=False)
    else:
        merged = merged.sort_values("abs_tipsy45_difference", ascending=False)
    return merged.to_dict(orient="records")


def _merge_au(
    default_run: Mapping[str, Any],
    tipsy45_run: Mapping[str, Any],
    market_run: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    default_au = pd.read_csv(Path(default_run["run_dir"]) / "au_sort_summary.csv")
    tipsy_au = pd.read_csv(Path(tipsy45_run["run_dir"]) / "au_sort_summary.csv")
    columns = ["au_id", "pooled_tvd", "mean_plot_tvd"]
    au = default_au[columns].merge(
        tipsy_au[columns],
        on="au_id",
        how="outer",
        suffixes=("_default", "_tipsy45"),
    )
    au["pooled_tvd_delta"] = au["pooled_tvd_tipsy45"] - au["pooled_tvd_default"]
    au["mean_plot_tvd_delta"] = (
        au["mean_plot_tvd_tipsy45"] - au["mean_plot_tvd_default"]
    )
    if market_run is not None:
        market_au = pd.read_csv(Path(market_run["run_dir"]) / "au_sort_summary.csv")
        market_au = market_au[columns].rename(
            columns={
                "pooled_tvd": "pooled_tvd_market_surface",
                "mean_plot_tvd": "mean_plot_tvd_market_surface",
            }
        )
        au = au.merge(market_au, on="au_id", how="outer")
        au["pooled_tvd_delta_vs_default"] = (
            au["pooled_tvd_market_surface"] - au["pooled_tvd_default"]
        )
        au["mean_plot_tvd_delta_vs_default"] = (
            au["mean_plot_tvd_market_surface"] - au["mean_plot_tvd_default"]
        )
        au["pooled_tvd_delta_vs_tipsy45"] = (
            au["pooled_tvd_market_surface"] - au["pooled_tvd_tipsy45"]
        )
        au["mean_plot_tvd_delta_vs_tipsy45"] = (
            au["mean_plot_tvd_market_surface"] - au["mean_plot_tvd_tipsy45"]
        )
    n_plots = default_au.set_index("au_id")["n_plots"]
    au["n_plots"] = au["au_id"].map(n_plots).fillna(0).astype(int)
    au = au.sort_values("n_plots", ascending=False)
    return au.to_dict(orient="records")


def build_comparison(
    default_run: Mapping[str, Any],
    tipsy45_run: Mapping[str, Any],
    market_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the pre-declared profile comparison surface."""
    before = _scope_metrics(default_run)
    after = _scope_metrics(tipsy45_run)
    metrics = {
        "default": {"label": default_run["label"], **before},
        "tipsy45": {"label": tipsy45_run["label"], **after},
        "delta": {
            key: _delta(after.get(key), before.get(key)) for key in METRIC_KEYS
        },
    }
    compared_profiles = [default_run["label"], tipsy45_run["label"]]
    if market_run is not None:
        market = _scope_metrics(market_run)
        metrics["market_surface"] = {"label": market_run["label"], **market}
        metrics["delta_market_vs_default"] = {
            key: _delta(market.get(key), before.get(key)) for key in METRIC_KEYS
        }
        metrics["delta_market_vs_tipsy45"] = {
            key: _delta(market.get(key), after.get(key)) for key in METRIC_KEYS
        }
        compared_profiles.append(market_run["label"])

    gap_closes = _gap_closes(before, after)
    if market_run is None:
        verdict = (
            "gap_closes_on_both_pooled_and_mean_plot_tvd"
            if gap_closes
            else "gap_does_not_close_on_both_pooled_and_mean_plot_tvd"
        )
        market_gap = None
    else:
        market = metrics["market_surface"]
        market_gap_vs_default = _gap_closes(before, market)
        market_gap_vs_tipsy45 = _gap_closes(after, market)
        market_gap = {
            "vs_default": market_gap_vs_default,
            "vs_tipsy45": market_gap_vs_tipsy45,
        }
        if market_gap_vs_default:
            verdict = "market_surface_closes_gap_vs_default"
        elif market_gap_vs_tipsy45:
            verdict = "market_surface_closes_gap_vs_tipsy45_but_not_default"
        else:
            verdict = "market_surface_does_not_close_gap"

    return {
        "status": "descriptive_not_tuned",
        "label": "industrial_sort_validation",
        "grade_status": "not_assigned",
        "official_grade_validation": False,
        "compared_profiles": compared_profiles,
        "metrics": metrics,
        "gap_closes": gap_closes,
        "market_gap": market_gap,
        "verdict": verdict,
        "sort_shares": _merge_sort_shares(default_run, tipsy45_run, market_run),
        "by_au": _merge_au(default_run, tipsy45_run, market_run),
        "caveats": [
            "The profiles are pre-declared; no parameter was tuned to improve TVD.",
            "Industrial sort recovery is not official BC log grade.",
            "TIPSY industrial-log curves are model output, not field measurements.",
            (
                "FAN$IER 4.5 does not publish numeric default log prices or sort "
                "relativities; the tipsy45 price scenario is documented as an assumption."
            ),
            (
                "The market-surface profile uses published BC Interior species x "
                "product averages. Coast grade letters are not mapped; peeler "
                "prices are not assigned; missing cells remain gaps."
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
    market = metrics.get("market_surface")
    delta = metrics["delta"]
    has_market = market is not None
    profile_names = "`default` vs `tipsy45`"
    if has_market:
        profile_names = "`default` vs `tipsy45` vs `market_surface`"
    lines: list[str] = []
    lines.append("# TSA24 default vs tipsy45 vs market-surface bucking comparison\n")
    lines.append("| Field | Value |\n|---|---|\n")
    lines.append("| Validation | `industrial_sort_validation` |\n")
    lines.append("| Grade status | `not_assigned` |\n")
    lines.append("| Official grade validation | no |\n")
    lines.append(f"| Profiles | {profile_names} |\n")
    lines.append(f"| Verdict | `{comparison['verdict']}` |\n\n")
    lines.append(
        "> The profiles were declared before the comparison. No profile value was "
        "tuned to improve TVD. This is an industrial sort recovery check, not an "
        "official BC log-grade validation.\n\n"
    )
    metric_names = (
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
    rows = []
    for key in metric_names:
        row = {
            "metric": key,
            "default": default.get(key),
            "tipsy45": tipsy.get(key),
            "tipsy45_delta": delta.get(key),
        }
        if has_market:
            row["market_surface"] = market.get(key)
            row["market_vs_default"] = metrics["delta_market_vs_default"].get(key)
            row["market_vs_tipsy45"] = metrics["delta_market_vs_tipsy45"].get(key)
        rows.append(row)
    lines.append("## 1. Before/after metrics\n\n")
    columns = ["metric", "default", "tipsy45", "tipsy45_delta"]
    if has_market:
        columns = [
            "metric",
            "default",
            "tipsy45",
            "market_surface",
            "tipsy45_delta",
            "market_vs_default",
            "market_vs_tipsy45",
        ]
    lines.append(_markdown_table(rows, columns))
    lines.append(
        f"\nPilot logs: default **{default['n_logs']:,}**, tipsy45 "
        f"**{tipsy['n_logs']:,}**"
    )
    if has_market:
        lines.append(f", market_surface **{market['n_logs']:,}**")
    lines.append(
        f" on the same **{default['n_plots']}** comparable AU-matched plots and "
        f"**{default['n_trees']:,}** trees.\n\n"
    )
    if has_market:
        gap = comparison.get("market_gap") or {}
        lines.append(
            "Market-surface gap check: "
            f"vs default `{gap.get('vs_default')}`, "
            f"vs tipsy45 `{gap.get('vs_tipsy45')}`. "
            "A gap closes only when both pooled and mean per-plot TVD decline.\n\n"
        )

    lines.append("## 2. Sort shares\n\n")
    lines.append(
        "`abs_difference_change` is the tipsy45 absolute pilot-vs-TIPSY share "
        "difference minus the default difference; negative values are improvements. "
        "For the market surface, `abs_market_vs_default_change` and "
        "`abs_market_vs_tipsy45_change` use the same sign convention.\n\n"
    )
    sort_rows = comparison["sort_shares"][:12]
    sort_columns = [
        "code",
        "default_pilot_share",
        "default_tipsy_share",
        "tipsy45_pilot_share",
        "tipsy45_tipsy_share",
        "abs_difference_change",
    ]
    if has_market:
        sort_columns = [
            "code",
            "default_pilot_share",
            "default_tipsy_share",
            "tipsy45_pilot_share",
            "tipsy45_tipsy_share",
            "market_surface_pilot_share",
            "market_surface_tipsy_share",
            "abs_market_vs_default_change",
            "abs_market_vs_tipsy45_change",
        ]
    lines.append(_markdown_table(sort_rows, sort_columns))
    lines.append("\n## 3. Non-TIPSY nominal lengths\n\n")
    lines.append(
        "Counts of bucked logs whose exact length is outside the nominal TIPSY "
        "length list. This diagnostic is descriptive; TIPSY sort codes are "
        "minimum-length classes, so it is not a grade claim.\n\n"
    )
    length_keys = set(default["non_tipsy_lengths"] or {}) | set(
        tipsy["non_tipsy_lengths"] or {}
    )
    if has_market:
        length_keys |= set(market["non_tipsy_lengths"] or {})
    length_rows = []
    for length in sorted(length_keys):
        row = {
            "length_m": length,
            "default_logs": (default["non_tipsy_lengths"] or {}).get(length, 0),
            "tipsy45_logs": (tipsy["non_tipsy_lengths"] or {}).get(length, 0),
        }
        if has_market:
            row["market_surface_logs"] = (market["non_tipsy_lengths"] or {}).get(length, 0)
        length_rows.append(row)
    length_columns = ["length_m", "default_logs", "tipsy45_logs"]
    if has_market:
        length_columns.append("market_surface_logs")
    lines.append(_markdown_table(length_rows, length_columns))
    lines.append("\n## 4. Per-AU TVD\n\n")
    au_rows = comparison["by_au"][:20]
    au_columns = [
        "au_id",
        "n_plots",
        "pooled_tvd_default",
        "pooled_tvd_tipsy45",
        "pooled_tvd_delta",
        "mean_plot_tvd_delta",
    ]
    if has_market:
        au_columns = [
            "au_id",
            "n_plots",
            "pooled_tvd_default",
            "pooled_tvd_tipsy45",
            "pooled_tvd_market_surface",
            "pooled_tvd_delta_vs_default",
            "pooled_tvd_delta_vs_tipsy45",
        ]
    lines.append(_markdown_table(au_rows, au_columns))
    lines.append("\n## 5. What remains\n\n")
    lines.append(
        "- The tipsy45 geometry removes every non-nominal 7.5 m and 10.0 m log. "
        "The market surface changes the product prices and product assignment, "
        "not the geometry.\n"
    )
    lines.append(
        "- The value-maximising dynamic program over-selects 13 m and 11 m logs "
        "and under-selects the 8 m and 5.1 m classes when the published price is "
        "a per-cubic-metre product average with no length premium.\n"
    )
    lines.append(
        "- BC log-market reports publish species x product averages, not "
        "TIPSY-sort-specific or grade-specific prices. Coast grade letters have "
        "no documented crosswalk to TIPSY industrial dimension sorts; peeler "
        "prices cannot be assigned without quality. Those cells remain gaps, not "
        "invented values.\n"
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
        "--market-dir",
        type=Path,
        default=None,
        help="Optional market-surface validation run to include in a three-way comparison.",
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
    market_run = (
        load_run(args.market_dir, label="market_surface")
        if args.market_dir is not None
        else None
    )
    comparison = build_comparison(default_run, tipsy45_run, market_run)
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
