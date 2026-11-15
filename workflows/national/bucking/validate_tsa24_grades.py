"""Validate the TSA24 bucking pilot against compiled TIPSY grade curves.

The pilot's ``sort_code`` values are TIPSY 4.5 industrial log dimensions, not
BC log grades.  This module therefore reports two separate surfaces:

* an industrial-sort share comparison, where pilot and TIPSY use the same
  code family; and
* a TIPSY ``Logs_Grade_*`` share table for context, labelled as a grade-group
  proxy because the pilot has ``grade_status=not_assigned``.

It also reports TIPSY residual fibre next to the pilot harvest-residue ledger.
Those two quantities are not equivalent: TIPSY residuals are mill-processing
chips, sawdust, shavings, trim, and bark, while the pilot residue is
``taper total - recovered logs``.  The output records that semantic gap
instead of treating the ratio as an error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .tsa24_grade_curves import GRADE_NAMES, INDUSTRIAL_NAMES

__all__ = [
    "GRADE_ORDER",
    "INDUSTRIAL_ORDER",
    "benchmark_grade_residue",
    "normalise_sort_code",
]

GRADE_ORDER: tuple[str, ...] = tuple(name.removeprefix("Logs_Grade_") for name in GRADE_NAMES)
INDUSTRIAL_ORDER: tuple[str, ...] = tuple(
    name.removeprefix("Industrial_Logs_") for name in INDUSTRIAL_NAMES
)
PRODUCT_ORDER: tuple[str, ...] = ("sawlog", "other", "pulpwood")
DIAMETER_ORDER: tuple[str, ...] = ("D125", "D20", "D30", "D38")
LENGTH_ORDER: tuple[str, ...] = ("L5", "L51", "L63", "L8", "L11", "L13")

_SORT_PATTERN = re.compile(r"^(?P<diameter>D\d+)(?P<length>L\d+)$")

#: The pilot rule set spells the 5.0 m pulpwood sort ``D125L50``; TIPSY uses
#: ``D125L5``.  The mapping is explicit so a future rule rename fails visibly.
_SORT_ALIASES: Mapping[str, str] = {"D125L50": "D125L5"}

_SPECIES_TO_CURVE: Mapping[str, str] = {
    "SW": "SW",
    "SE": "SE",
    "BL": "BL",
    "S": "SW",
    "SB": "SW",
    "SX": "SW",
    "PL": "PLI",
    "PLI": "PLI",
    "PLC": "PLI",
    "FD": "FDI",
    "FDI": "FDI",
    "AT": "AT",
    "CW": "CW",
    "HW": "HWI",
    "HWI": "HWI",
}


def normalise_sort_code(value: str) -> str:
    """Normalise a pilot sort code to the TIPSY industrial code family."""
    code = str(value).strip()
    return _SORT_ALIASES.get(code, code)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_interp(x: float, xs: np.ndarray, ys: np.ndarray) -> float:
    finite = np.isfinite(xs) & np.isfinite(ys)
    if not finite.any():
        return float("nan")
    return float(
        np.interp(
            float(x),
            xs[finite],
            ys[finite],
            left=0.0,
            right=float(ys[finite][-1]),
        )
    )


def _row_at_age(frame: pd.DataFrame, age: float) -> dict[str, float]:
    """Interpolate every numeric column of a curve table at ``age``."""
    if frame.empty:
        return {}
    ordered = frame.sort_values("age")
    xs = pd.to_numeric(ordered["age"], errors="coerce").to_numpy(dtype=float)
    values: dict[str, float] = {}
    for column in ordered.columns:
        if column == "age":
            continue
        numeric = pd.to_numeric(ordered[column], errors="coerce")
        if numeric.notna().sum() == 0:
            continue
        values[column] = _finite_interp(age, xs, numeric.to_numpy(dtype=float))
    values["age"] = float(age)
    return values


def _normalised_shares(values: Mapping[str, float], order: Sequence[str]) -> dict[str, float]:
    total = sum(max(0.0, float(values.get(key, 0.0) or 0.0)) for key in order)
    if total <= 0:
        return {key: 0.0 for key in order}
    return {
        key: max(0.0, float(values.get(key, 0.0) or 0.0)) / total
        for key in order
    }


def _prefixed_shares(
    values: Mapping[str, float],
    prefix: str,
    order: Sequence[str],
) -> dict[str, float]:
    return _normalised_shares(
        {key: float(values.get(f"{prefix}{key}", 0.0) or 0.0) for key in order},
        order,
    )


def _volume_shares(
    frame: pd.DataFrame,
    column: str,
    order: Sequence[str],
    *,
    volume_column: str = "volume_m3",
) -> dict[str, float]:
    if frame.empty:
        return {key: 0.0 for key in order}
    totals = (
        frame.groupby(column)[volume_column]
        .sum()
        .reindex(list(order), fill_value=0.0)
        .astype(float)
    )
    total = float(totals.sum())
    if total <= 0:
        return {key: 0.0 for key in order}
    return {str(key): float(value) / total for key, value in totals.items()}


def _split_sort_code(code: str) -> tuple[str | None, str | None]:
    match = _SORT_PATTERN.fullmatch(str(code))
    if match is None:
        return None, None
    return match.group("diameter"), match.group("length")


def _class_shares(
    shares: Mapping[str, float],
    order: Sequence[str],
    *,
    kind: str,
) -> dict[str, float]:
    totals = {key: 0.0 for key in order}
    for code, share in shares.items():
        diameter, length = _split_sort_code(code)
        key = diameter if kind == "diameter" else length
        if key in totals:
            totals[key] += float(share)
    total = sum(totals.values())
    if total <= 0:
        return {key: 0.0 for key in order}
    return {key: value / total for key, value in totals.items()}


def _share_distance(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> dict[str, float]:
    keys = sorted(set(left) | set(right))
    left_values = np.array([float(left.get(key, 0.0)) for key in keys], dtype=float)
    right_values = np.array([float(right.get(key, 0.0)) for key in keys], dtype=float)
    absolute = np.abs(left_values - right_values)
    return {
        "total_variation_distance": float(0.5 * absolute.sum()),
        "overlap": float(np.minimum(left_values, right_values).sum()),
        "l1": float(absolute.sum()),
    }


def _mean_class_distance(
    frame: pd.DataFrame,
    pilot_prefix: str,
    tipsy_prefix: str,
    order: Sequence[str],
) -> dict[str, Any]:
    distances = []
    for _, row in frame.iterrows():
        distances.append(
            _share_distance(
                {key: float(row[f"{pilot_prefix}{key}"]) for key in order},
                {key: float(row[f"{tipsy_prefix}{key}"]) for key in order},
            )
        )
    if not distances:
        return {"n": 0, "mean_tvd": None, "mean_overlap": None}
    return {
        "n": len(distances),
        "mean_tvd": float(np.mean([item["total_variation_distance"] for item in distances])),
        "mean_overlap": float(np.mean([item["overlap"] for item in distances])),
    }


def _load_compiled(compiled_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    paths = {
        "managed": compiled_dir / "managed_au_curves.csv",
        "coverage": compiled_dir / "au_grade_coverage.csv",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "compiled TSA24 grade curves are missing: " + ", ".join(missing)
        )
    industrial_path = compiled_dir / "managed_au_industrial_sorts.csv"
    species_path = compiled_dir / "species_grade_curves.csv"
    industrial = pd.read_csv(industrial_path) if industrial_path.exists() else pd.DataFrame()
    species = pd.read_csv(species_path) if species_path.exists() else pd.DataFrame()
    return pd.read_csv(paths["managed"]), industrial, pd.read_csv(paths["coverage"]), species


def _au_lookup(coverage: pd.DataFrame) -> dict[tuple[str, str], int]:
    lookup: dict[tuple[str, str], int] = {}
    for row in coverage.itertuples(index=False):
        if pd.isna(row.stratum_code) or pd.isna(row.si_level):
            continue
        lookup[(str(row.stratum_code), str(row.si_level))] = int(row.au_id)
    return lookup


def _plot_attrs(
    attrs: pd.DataFrame,
    plot_id: str,
) -> Mapping[str, Any] | None:
    matches = attrs[attrs["plot_id"].astype(str) == str(plot_id)]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def _grade_group_shares(shares: Mapping[str, float]) -> dict[str, float]:
    return {
        "high_grade_D_F_H_I": sum(shares.get(key, 0.0) for key in ("D", "F", "H", "I")),
        "sawlog_group_D_F_H_I_J": sum(
            shares.get(key, 0.0) for key in ("D", "F", "H", "I", "J")
        ),
        "low_grade_U_X_Y": sum(shares.get(key, 0.0) for key in ("U", "X", "Y")),
    }


def _aggregate_shares(
    rows: Sequence[Mapping[str, Any]],
    key: str,
    weights: Sequence[float],
) -> dict[str, float]:
    total_weight = sum(max(0.0, float(weight)) for weight in weights)
    if total_weight <= 0:
        return {}
    keys = sorted({code for row in rows for code in row.get(key, {})})
    return {
        code: sum(
            float(row.get(key, {}).get(code, 0.0)) * max(0.0, float(weight))
            for row, weight in zip(rows, weights)
        )
        / total_weight
        for code in keys
    }


def _summary_metrics(
    values: Sequence[float],
) -> dict[str, float | int | None]:
    clean = np.array([float(value) for value in values if math.isfinite(float(value))])
    if clean.size == 0:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "min": float(clean.min()),
        "max": float(clean.max()),
    }


def _benchmark_metrics(
    frame: pd.DataFrame,
    observed_column: str,
    predicted_column: str,
) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    observed = pd.to_numeric(frame[observed_column], errors="coerce")
    predicted = pd.to_numeric(frame[predicted_column], errors="coerce")
    valid = observed.notna() & predicted.notna()
    if not valid.any():
        return {"n": 0}
    observed = observed[valid].astype(float)
    predicted = predicted[valid].astype(float)
    bias = predicted - observed
    return {
        "n": int(valid.sum()),
        "observed_sum": float(observed.sum()),
        "predicted_sum": float(predicted.sum()),
        "mean_bias": float(bias.mean()),
        "mae": float(bias.abs().mean()),
        "rmse": float(np.sqrt(np.mean(np.square(bias)))),
        "relative_bias": float(bias.sum() / observed.sum()) if observed.sum() > 0 else None,
        "mean_ratio": float((predicted / observed).replace([np.inf, -np.inf], np.nan).dropna().mean())
        if (observed > 0).any()
        else None,
    }


def benchmark_grade_residue(
    *,
    attrs: pd.DataFrame,
    plot_summary: pd.DataFrame,
    logs: pd.DataFrame,
    tree_summary: pd.DataFrame,
    compiled_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Benchmark pilot sort, grade, and residue surfaces against TIPSY TAF.

    Parameters
    ----------
    attrs : pandas.DataFrame
        Plot attributes from ``_plot_benchmark_attributes``, including
        ``plot_id``, ``stratum_code``, and ``si_level``.
    plot_summary : pandas.DataFrame
        Pilot per-plot summary.
    logs : pandas.DataFrame
        Pilot bucked logs.
    tree_summary : pandas.DataFrame
        Pilot per-tree summary; used only for a secondary residue audit.
    compiled_dir : pathlib.Path
        Directory written by ``compile_tsa24_grade_curves``.

    Returns
    -------
    summary : dict
        Machine-readable benchmark summary.
    frame : pandas.DataFrame
        One row per pilot plot with matched TIPSY share values.
    """
    compiled_dir = Path(compiled_dir)
    managed, industrial, coverage, species = _load_compiled(compiled_dir)
    au_lookup = _au_lookup(coverage)
    managed_by_au = {
        int(au): group.sort_values("age") for au, group in managed.groupby("au_id")
    }
    industrial_by_au = {
        int(au): group.sort_values("age")
        for au, group in industrial.groupby("au_id")
    } if not industrial.empty else {}
    species_by_code = {
        str(code): group.sort_values("age")
        for code, group in species.groupby("species_code")
    } if not species.empty else {}
    industrial_available = bool(industrial_by_au) and all(
        f"Industrial_Logs_{code}" in industrial.columns for code in INDUSTRIAL_ORDER
    )
    pilot_logs = logs.copy()
    pilot_logs["sort_code"] = pilot_logs["sort_code"].map(normalise_sort_code)

    rows: list[dict[str, Any]] = []
    for plot in plot_summary.itertuples(index=False):
        plot_id = str(plot.plot_id)
        attr = _plot_attrs(attrs, plot_id)
        plot_logs = pilot_logs[pilot_logs["plot_id"].astype(str) == plot_id]
        bucked_volume = float(plot_logs["volume_m3"].sum()) if not plot_logs.empty else 0.0
        plot_sort_shares = _volume_shares(plot_logs, "sort_code", INDUSTRIAL_ORDER)
        plot_product_shares = _volume_shares(plot_logs, "product", PRODUCT_ORDER)
        age = float(plot.stand_age) if pd.notna(plot.stand_age) else float("nan")
        stratum = str(attr["stratum_code"]) if attr and pd.notna(attr.get("stratum_code")) else None
        si_level = str(attr["si_level"]) if attr and pd.notna(attr.get("si_level")) else None
        au_value = attr.get("au_id") if attr else None
        au_id = (
            int(au_value)
            if au_value is not None and pd.notna(au_value)
            else (au_lookup.get((stratum, si_level)) if stratum and si_level else None)
        )

        match_tier = "no_match"
        matched_curve: dict[str, float] = {}
        industrial_row: dict[str, float] = {}
        tipsy_volume = float("nan")
        tipsy_residue = float("nan")
        tipsy_residue_per_m3 = float("nan")
        tipsy_grade_shares = {key: 0.0 for key in GRADE_ORDER}
        tipsy_industrial_shares = {key: 0.0 for key in INDUSTRIAL_ORDER}
        grade_coverage = float("nan")
        extrapolated = False

        if au_id is not None and au_id in managed_by_au and math.isfinite(age):
            matched_curve = _row_at_age(managed_by_au[au_id], age)
            industrial_row = (
                _row_at_age(industrial_by_au[au_id], age)
                if au_id in industrial_by_au
                else {}
            )
            tipsy_volume = float(
                matched_curve.get(
                    "volume", matched_curve.get("taf_volume125_m3ha", float("nan"))
                )
            )
            tipsy_residue = float(matched_curve.get("residual_fibre_odt_ha", float("nan")))
            tipsy_residue_per_m3 = float(
                matched_curve.get("residue_odt_per_m3", float("nan"))
            )
            tipsy_grade_shares = _prefixed_shares(
                matched_curve, "Logs_Grade_", GRADE_ORDER
            )
            tipsy_industrial_shares = _prefixed_shares(
                industrial_row, "Industrial_Logs_", INDUSTRIAL_ORDER
            )
            grade_coverage = float(
                matched_curve.get("grade_coverage_fraction", float("nan"))
            )
            extrapolated = bool(
                matched_curve.get(
                    "age_extrapolated", matched_curve.get("taf_extrapolated", 0.0)
                )
            )
            match_tier = "au_curve"
        else:
            curve_code = _SPECIES_TO_CURVE.get(str(plot.leading_species).upper())
            if curve_code in species_by_code and math.isfinite(age):
                matched_curve = _row_at_age(species_by_code[curve_code], age)
                tipsy_volume = float(
                    matched_curve.get(
                        "volume",
                        matched_curve.get("taf_volume125_m3ha", float("nan")),
                    )
                )
                tipsy_residue = float(
                    matched_curve.get(
                        "residual_fibre_odt_ha",
                        matched_curve.get("taf_residual_fibre_odt_ha", float("nan")),
                    )
                )
                if tipsy_volume and tipsy_volume > 0:
                    tipsy_residue_per_m3 = tipsy_residue / tipsy_volume
                tipsy_grade_shares = _prefixed_shares(
                    matched_curve, "Logs_Grade_", GRADE_ORDER
                )
                tipsy_industrial_shares = _prefixed_shares(
                    matched_curve, "Industrial_Logs_", INDUSTRIAL_ORDER
                )
                grade_total = sum(
                    float(matched_curve.get(name, 0.0) or 0.0) for name in GRADE_NAMES
                )
                if tipsy_volume and tipsy_volume > 0:
                    grade_coverage = float(
                        matched_curve.get(
                            "grade_coverage_fraction", grade_total / tipsy_volume
                        )
                    )
                extrapolated = bool(
                    matched_curve.get(
                        "age_extrapolated",
                        matched_curve.get("taf_extrapolated", 0.0),
                    )
                )
                match_tier = "species_curve"

        grade_groups = _grade_group_shares(tipsy_grade_shares)
        pilot_diameter = _class_shares(plot_sort_shares, DIAMETER_ORDER, kind="diameter")
        pilot_length = _class_shares(plot_sort_shares, LENGTH_ORDER, kind="length")
        tipsy_diameter = _class_shares(
            tipsy_industrial_shares, DIAMETER_ORDER, kind="diameter"
        )
        tipsy_length = _class_shares(
            tipsy_industrial_shares, LENGTH_ORDER, kind="length"
        )
        rows.append(
            {
                "plot_id": plot_id,
                "bec_zone": plot.bec_zone,
                "leading_species": plot.leading_species,
                "stand_age": age,
                "site_index": plot.site_index,
                "stratum_code": stratum,
                "si_level": si_level,
                "au_id": au_id,
                "match_tier": match_tier,
                "age_extrapolated": extrapolated,
                "bucked_log_m3": bucked_volume,
                "pilot_sawlog_share": plot_product_shares.get("sawlog", 0.0),
                "pilot_other_share": plot_product_shares.get("other", 0.0),
                "pilot_pulpwood_share": plot_product_shares.get("pulpwood", 0.0),
                "tipsy_volume_m3ha": tipsy_volume,
                "tipsy_residual_fibre_odt_ha": tipsy_residue,
                "tipsy_residue_odt_per_m3": tipsy_residue_per_m3,
                "pilot_residue_odt_ha": float(plot.residue_odt_ha),
                "pilot_taper_total_m3ha": float(plot.taper_total_m3ha),
                "tipsy_grade_coverage": grade_coverage,
                "tipsy_sawlog_group_share": grade_groups["sawlog_group_D_F_H_I_J"],
                **{
                    f"pilot_sort_{code}": plot_sort_shares.get(code, 0.0)
                    for code in INDUSTRIAL_ORDER
                },
                **{
                    f"tipsy_sort_{code}": tipsy_industrial_shares.get(code, 0.0)
                    for code in INDUSTRIAL_ORDER
                },
                **{
                    f"tipsy_grade_{code}": tipsy_grade_shares.get(code, 0.0)
                    for code in GRADE_ORDER
                },
                **{
                    f"pilot_diam_{code}": pilot_diameter.get(code, 0.0)
                    for code in DIAMETER_ORDER
                },
                **{
                    f"pilot_len_{code}": pilot_length.get(code, 0.0)
                    for code in LENGTH_ORDER
                },
                **{
                    f"tipsy_diam_{code}": tipsy_diameter.get(code, 0.0)
                    for code in DIAMETER_ORDER
                },
                **{
                    f"tipsy_len_{code}": tipsy_length.get(code, 0.0)
                    for code in LENGTH_ORDER
                },
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return (
            {
                "status": "no_plots",
                "compiled_dir": str(compiled_dir),
            },
            frame,
        )

    matched = frame[frame["match_tier"] != "no_match"].copy()
    comparable_grade = matched[
        (matched["bucked_log_m3"] > 0)
        & (matched["tipsy_volume_m3ha"] > 0)
        & (matched["tipsy_grade_coverage"] > 0)
    ].copy()
    comparable_sort = (
        matched[
            (matched["bucked_log_m3"] > 0)
            & (matched["tipsy_volume_m3ha"] > 0)
            & (matched["tipsy_grade_coverage"] >= 0.99)
        ].copy()
        if industrial_available
        else matched.iloc[0:0].copy()
    )
    frame["sort_comparable"] = frame.index.isin(comparable_sort.index)
    sort_distances = []
    for row in matched.itertuples(index=False):
        pilot_shares = {
            code: float(getattr(row, f"pilot_sort_{code}")) for code in INDUSTRIAL_ORDER
        }
        tipsy_shares = {
            code: float(getattr(row, f"tipsy_sort_{code}")) for code in INDUSTRIAL_ORDER
        }
        sort_distances.append(_share_distance(pilot_shares, tipsy_shares))
    for index, distance in zip(matched.index, sort_distances):
        frame.loc[index, "sort_tvd"] = distance["total_variation_distance"]
        frame.loc[index, "sort_overlap"] = distance["overlap"]

    weights = comparable_sort["bucked_log_m3"].astype(float).tolist()
    aggregate_pilot_sort = (
        _volume_shares(
            pilot_logs[
                pilot_logs["plot_id"].astype(str).isin(
                    comparable_sort["plot_id"].astype(str)
                )
            ],
            "sort_code",
            INDUSTRIAL_ORDER,
        )
        if not comparable_sort.empty
        else {}
    )
    # Convert the flat tipsy_sort_* columns into nested dicts for aggregation.
    tipsy_sort_records = [
        {
            "tipsy_sort": {
                code: float(row[f"tipsy_sort_{code}"]) for code in INDUSTRIAL_ORDER
            }
        }
        for _, row in comparable_sort.iterrows()
    ]
    aggregate_tipsy_sort = _aggregate_shares(tipsy_sort_records, "tipsy_sort", weights)
    aggregate_grade = _aggregate_shares(
        [
            {
                "grade": {
                    code: float(row[f"tipsy_grade_{code}"]) for code in GRADE_ORDER
                }
            }
            for _, row in comparable_grade.iterrows()
        ],
        "grade",
        comparable_grade["bucked_log_m3"].astype(float).tolist(),
    )
    comparable_logs = pilot_logs[
        pilot_logs["plot_id"].astype(str).isin(comparable_grade["plot_id"].astype(str))
    ]
    pilot_product_aggregate = _volume_shares(
        comparable_logs, "product", PRODUCT_ORDER
    ) if not comparable_logs.empty else {key: 0.0 for key in PRODUCT_ORDER}
    aggregate_pilot_diameter = _class_shares(
        aggregate_pilot_sort, DIAMETER_ORDER, kind="diameter"
    )
    aggregate_tipsy_diameter = _class_shares(
        aggregate_tipsy_sort, DIAMETER_ORDER, kind="diameter"
    )
    aggregate_pilot_length = _class_shares(
        aggregate_pilot_sort, LENGTH_ORDER, kind="length"
    )
    aggregate_tipsy_length = _class_shares(
        aggregate_tipsy_sort, LENGTH_ORDER, kind="length"
    )

    residue_frame = frame[frame["match_tier"] == "au_curve"].copy()
    residue_per_m3_frame = frame[frame["match_tier"] == "au_curve"].copy()
    residue_per_m3_frame["pilot_residue_odt_per_m3"] = (
        residue_per_m3_frame["pilot_residue_odt_ha"]
        / residue_per_m3_frame["pilot_taper_total_m3ha"].replace(0, np.nan)
    )

    summary = {
        "status": "reported_with_join_and_semantic_assumptions",
        "compiled_dir": str(compiled_dir),
        "n_plots_total": int(len(frame)),
        "n_age_missing": int(frame["stand_age"].isna().sum()),
        "n_au_curve": int((frame["match_tier"] == "au_curve").sum()),
        "n_species_curve": int((frame["match_tier"] == "species_curve").sum()),
        "n_no_match": int((frame["match_tier"] == "no_match").sum()),
        "n_grade_comparable": int(len(comparable_grade)),
        "n_zero_pilot_logs": int((frame["bucked_log_m3"] <= 0).sum()),
        "n_zero_tipsy_volume": int(
            ((frame["tipsy_volume_m3ha"] <= 0) & (frame["match_tier"] != "no_match")).sum()
        ),
        "sort_share_comparison": {
            "status": (
                "reported"
                if industrial_available
                else "not_available_no_industrial_logs_bank"
            ),
            "n": int(len(comparable_sort)),
            "mean_tvd": _summary_metrics(
                frame.loc[frame["sort_comparable"], "sort_tvd"].dropna().tolist()
            ),
            "mean_overlap": _summary_metrics(
                frame.loc[frame["sort_comparable"], "sort_overlap"].dropna().tolist()
            ),
            "aggregate_pilot": aggregate_pilot_sort,
            "aggregate_tipsy": aggregate_tipsy_sort,
            "aggregate_distance": (
                _share_distance(aggregate_pilot_sort, aggregate_tipsy_sort)
                if industrial_available
                and aggregate_pilot_sort
                and aggregate_tipsy_sort
                else None
            ),
            "code_alias": dict(_SORT_ALIASES),
            "note": (
                "The refreshed TSA24 BTC run used log-grades and residual-fibre "
                "banks; it did not request industrial-logs, so the industrial "
                "sort surface is not comparable in this pass."
                if not industrial_available
                else "Pilot and TIPSY sort codes share the industrial code family."
            ),
        },
        "diameter_share_comparison": {
            "n": int(len(comparable_sort)),
            "mean": _mean_class_distance(
                comparable_sort, "pilot_diam_", "tipsy_diam_", DIAMETER_ORDER
            ),
            "aggregate_pilot": aggregate_pilot_diameter,
            "aggregate_tipsy": aggregate_tipsy_diameter,
            "aggregate_distance": (
                _share_distance(aggregate_pilot_diameter, aggregate_tipsy_diameter)
                if industrial_available
                else None
            ),
        },
        "length_share_comparison": {
            "n": int(len(comparable_sort)),
            "mean": _mean_class_distance(
                comparable_sort, "pilot_len_", "tipsy_len_", LENGTH_ORDER
            ),
            "aggregate_pilot": aggregate_pilot_length,
            "aggregate_tipsy": aggregate_tipsy_length,
            "aggregate_distance": (
                _share_distance(aggregate_pilot_length, aggregate_tipsy_length)
                if industrial_available
                else None
            ),
        },
        "grade_share_comparison": {
            "status": "proxy_only_pilot_grade_status_not_assigned",
            "n": int(len(comparable_grade)),
            "aggregate_tipsy_grades": aggregate_grade,
            "aggregate_tipsy_groups": _grade_group_shares(aggregate_grade),
            "pilot_product_shares": pilot_product_aggregate,
            "pilot_sawlog_product_share": pilot_product_aggregate.get("sawlog", 0.0),
            "note": (
                "Pilot sort codes are dimension-only industrial sorts, not BC "
                "log grades.  TIPSY Logs_Grade_* values are BTC model output, "
                "not field grades; compare grade groups only as a proxy."
            ),
        },
        "residue_comparison": {
            "status": "not_comparable_by_design",
            "au_curve_n": int(len(residue_frame)),
            "pilot_taper_residue_odt_ha_sum": float(
                residue_frame["pilot_residue_odt_ha"].sum()
            ),
            "pilot_taper_residue_odt_per_m3_mean": float(
                residue_per_m3_frame["pilot_residue_odt_per_m3"]
                .replace([np.inf, -np.inf], np.nan)
                .mean()
            ),
            "note": (
                "BTC Residual_Chips/Sawdust/Shavings/Trim/Bark are "
                "mill-processing residues. The pilot taper residue is harvest "
                "slash (top/stump/unmerchantable). The two are not equivalent, "
                "so no ratio or error metric is computed. Harvest slash is "
                "reported separately from the biomass-live AGB bank."
            ),
        },
        "value_comparison": {
            "status": "not_computed_no_same_basis_grade_prices",
            "note": (
                "The pilot uses product-level BC Interior log prices.  No "
                "same-basis interior grade price vector is available, so only "
                "shares are reported."
            ),
        },
        "join_basis": (
            "FAIB plot -> BEC + leading species -> managed FEMIC AU stratum "
            "(including combined-species strata) -> FEMIC AU site-index class -> "
            "managed AU; BTC /TSR curve interpolated at FAIB stand age. Species "
            "fallback uses a mean of the same-species managed AU curves and is "
            "labelled separately."
        ),
        "caveats": [
            (
                "Grade and residue values are direct BTC /TSR model output at the "
                "AU site index; no nominal-site-index TAF substitution is applied."
            ),
            "Logs_Grade_D and Logs_Grade_F are zero for every TSA24 AU in this run.",
            "Pilot sort codes are not official BC log grades.",
            "TIPSY residual fibre is mill residue, not harvest residue.",
            (
                "Industrial sort comparison is unavailable because the "
                "industrial-logs bank was not requested."
            ),
        ],
    }
    return summary, frame


def _default_compiled_dir() -> Path:
    return Path("data/derived/national/bucking/tsa24_grade_curves")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the TSA24 bucking pilot against compiled TIPSY grade curves."
    )
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--compiled-dir", type=Path, default=_default_compiled_dir())
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    from .tree_lists import load_faib_psp_tree_list
    from .validate_tsa24 import _plot_benchmark_attributes, default_femic_root

    args = build_parser().parse_args(argv)
    pilot_dir = Path(args.pilot_dir)
    plot_summary = pd.read_csv(pilot_dir / "plot_summary.csv")
    logs = pd.read_csv(pilot_dir / "bucked_logs.csv")
    tree_summary = pd.read_csv(pilot_dir / "tree_summary.csv")
    faib = load_faib_psp_tree_list(Path("data/raw"))
    attrs = _plot_benchmark_attributes(faib, default_femic_root())
    summary, frame = benchmark_grade_residue(
        attrs=attrs,
        plot_summary=plot_summary,
        logs=logs,
        tree_summary=tree_summary,
        compiled_dir=args.compiled_dir,
    )
    summary["compiled_dir"] = str(args.compiled_dir)
    out_dir = Path(args.out_dir) if args.out_dir else pilot_dir
    frame.to_csv(out_dir / "grade_benchmark.csv", index=False, float_format="%.6f")
    (out_dir / "grade_benchmark_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
