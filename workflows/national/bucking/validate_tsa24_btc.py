"""BTC volume validation and harvest-slash residue ledger for the TSA24 pilot.

The module deliberately separates three surfaces that are often conflated:

* merchantable volume, from the compiled BTC ``managed_au_curves.csv``;
* live aboveground biomass, from the FEMIC BTC ``biomass-live`` bank; and
* harvest slash, defined as ``AGB - merchantable_log_biomass``.

It does **not** use the BTC ``Residual_*`` columns.  Those are TIPSY/SYLVER
mill-processing residues (chips, sawdust, shavings, trim, bark), not logging
slash.  The residue ledger uses the pilot's bucked log volumes and a
species-specific basic wood density table.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "DEFAULT_CHIP_YIELD",
    "DEFAULT_MOISTURE_GREEN",
    "DEFAULT_MOISTURE_SEASONED",
    "DEFAULT_RECOVERY_FACTOR",
    "DEFAULT_VOLUME_RATIO_MAX",
    "DEFAULT_VOLUME_RATIO_MIN",
    "btc_volume_benchmark",
    "harvest_residue_ledger",
    "load_basic_density_table",
    "resolve_species_density",
]

DEFAULT_RECOVERY_FACTOR = 0.60
DEFAULT_MOISTURE_GREEN = 0.45
DEFAULT_MOISTURE_SEASONED = 0.30
DEFAULT_CHIP_YIELD = 0.90
DEFAULT_VOLUME_RATIO_MIN = 0.40
DEFAULT_VOLUME_RATIO_MAX = 2.50
DENSITY_RANGE_ODT_M3 = (0.20, 0.80)


def load_basic_density_table(path: Path) -> dict[str, dict[str, Any]]:
    """Load and parse the species basic-density table.

    The table is a reference input, not a derived result.  Parsing at the
    boundary means every later lookup receives a positive density in the
    credible basic-density range.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"basic-density table not found: {path}")
    frame = pd.read_csv(path)
    required = {
        "species_code",
        "common_name",
        "basic_density_odt_m3",
        "source",
        "evidence",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"basic-density table missing columns: {', '.join(missing)}")

    table: dict[str, dict[str, Any]] = {}
    low, high = DENSITY_RANGE_ODT_M3
    for row in frame.itertuples(index=False):
        code = str(row.species_code).strip().upper()
        if not code:
            raise ValueError("basic-density table contains an empty species code")
        if code in table:
            raise ValueError(f"duplicate basic-density species code: {code}")
        density = float(row.basic_density_odt_m3)
        if not math.isfinite(density) or not low <= density <= high:
            raise ValueError(
                f"basic density for {code} is outside the credible range "
                f"{low}-{high} ODT/m3: {density}"
            )
        table[code] = {
            "species_code": code,
            "common_name": str(row.common_name),
            "basic_density_odt_m3": density,
            "source": str(row.source),
            "evidence": str(row.evidence),
        }
    if not table:
        raise ValueError("basic-density table is empty")
    return table


def resolve_species_density(
    species: str,
    table: Mapping[str, Mapping[str, Any]],
) -> float:
    """Return the species density or fail loudly on an unmapped code."""
    code = str(species).strip().upper()
    entry = table.get(code)
    if entry is None:
        raise KeyError(
            f"no basic-density entry for species {species!r}; "
            "add a documented row to rules/bc_basic_density.csv"
        )
    return float(entry["basic_density_odt_m3"])


def _finite_interp(age: float, curve: pd.DataFrame, column: str) -> float:
    ordered = curve.sort_values("age")
    xs = pd.to_numeric(ordered["age"], errors="coerce").to_numpy(dtype=float)
    ys = pd.to_numeric(ordered[column], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(xs) & np.isfinite(ys)
    if not finite.any():
        return float("nan")
    return float(np.interp(float(age), xs[finite], ys[finite]))


def _metrics(observed: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    observed_array = np.asarray(observed, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    valid = np.isfinite(observed_array) & np.isfinite(predicted_array)
    if not valid.any():
        return {"n": 0}
    observed_array = observed_array[valid]
    predicted_array = predicted_array[valid]
    bias = predicted_array - observed_array
    positive = observed_array > 0
    mean_ratio = (
        float((predicted_array[positive] / observed_array[positive]).mean())
        if positive.any()
        else None
    )
    return {
        "n": int(valid.sum()),
        "mean_observed": float(observed_array.mean()),
        "mean_predicted": float(predicted_array.mean()),
        "mean_bias_predicted_minus_observed": float(bias.mean()),
        "mae": float(np.abs(bias).mean()),
        "rmse": float(np.sqrt(np.mean(np.square(bias)))),
        "relative_bias": (
            float(bias.sum() / observed_array.sum())
            if observed_array.sum() > 0
            else None
        ),
        "mean_ratio_predicted_observed": mean_ratio,
    }


def _summary_stats(values: Sequence[float]) -> dict[str, Any]:
    clean = np.asarray([float(value) for value in values if math.isfinite(float(value))])
    if clean.size == 0:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "n": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "min": float(clean.min()),
        "max": float(clean.max()),
    }


def _plot_attribute(attrs: pd.DataFrame, plot_id: str) -> Mapping[str, Any] | None:
    matches = attrs[attrs["plot_id"].astype(str) == str(plot_id)]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def _curve_lookup(curves: pd.DataFrame, key: str, value: str) -> dict[int, pd.DataFrame]:
    return {
        int(au): group.sort_values("age") for au, group in curves.groupby(key)
    }


def _load_btc_curves(compiled_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    compiled_dir = Path(compiled_dir)
    curve_path = compiled_dir / "managed_au_curves.csv"
    coverage_path = compiled_dir / "au_grade_coverage.csv"
    missing = [str(path) for path in (curve_path, coverage_path) if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "compiled BTC curves are missing: " + ", ".join(missing)
        )
    curves = pd.read_csv(curve_path)
    coverage = pd.read_csv(coverage_path)
    required = {"au_id", "age", "volume", "stratum_code", "si_level", "leading_species"}
    absent = sorted(required.difference(curves.columns))
    if absent:
        raise ValueError(f"managed_au_curves.csv missing columns: {', '.join(absent)}")
    return curves, coverage


def btc_volume_benchmark(
    *,
    attrs: pd.DataFrame,
    plot_summary: pd.DataFrame,
    compiled_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Compare pilot merchantable volume with compiled BTC ``volume``.

    The comparison is made at the same AU and FAIB stand age used by the
    grade benchmark: BEC + leading species -> managed AU stratum -> FEMIC
    site-index class -> managed AU -> linear interpolation of the BTC curve.
    """
    curves, _coverage = _load_btc_curves(compiled_dir)
    curves_by_au = _curve_lookup(curves, "au_id", "au_id")

    rows: list[dict[str, Any]] = []
    for plot in plot_summary.itertuples(index=False):
        plot_id = str(plot.plot_id)
        attribute = _plot_attribute(attrs, plot_id)
        au_value = attribute.get("au_id") if attribute else None
        au_id = int(au_value) if au_value is not None and pd.notna(au_value) else None
        age = float(plot.stand_age) if pd.notna(plot.stand_age) else float("nan")
        stratum = (
            str(attribute["stratum_code"])
            if attribute and pd.notna(attribute.get("stratum_code"))
            else None
        )
        si_level = (
            str(attribute["si_level"])
            if attribute and pd.notna(attribute.get("si_level"))
            else None
        )

        if au_id is None:
            match_tier = "no_au_match"
            btc_volume = float("nan")
            reason = "no managed FEMIC AU for BEC + leading species + SI class"
        elif not math.isfinite(age):
            match_tier = "no_age"
            btc_volume = float("nan")
            reason = "FAIB stand age missing"
        elif au_id not in curves_by_au:
            match_tier = "no_curve"
            btc_volume = float("nan")
            reason = f"managed AU {au_id} has no compiled BTC curve"
        else:
            btc_volume = _finite_interp(age, curves_by_au[au_id], "volume")
            if not math.isfinite(btc_volume) or btc_volume <= 0:
                match_tier = "zero_btc_volume"
                reason = "BTC volume is zero or non-finite at the plot age"
            else:
                match_tier = "au_curve"
                reason = None

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
                "match_reason": reason,
                "btc_volume_m3ha": btc_volume,
                "observed_merchantable_all_m3ha": float(
                    plot.observed_merchantable_all_m3ha
                ),
                "observed_merchantable_sampled_m3ha": float(
                    plot.observed_merchantable_sampled_m3ha
                ),
                "pilot_bucked_log_m3ha": float(plot.bucked_log_m3ha),
                "pilot_bucked_log_m3ha_ratio_est": float(
                    plot.bucked_log_m3ha_ratio_est
                ),
            }
        )

    frame = pd.DataFrame(rows)
    matched = frame[frame["match_tier"] == "au_curve"].copy()
    unmatched = frame[frame["match_tier"] != "au_curve"].copy()

    summary = {
        "status": "reported_with_join_assumptions" if not matched.empty else "no_matched_plots",
        "n_plots_total": len(frame),
        "n_au_matched": int(frame["au_id"].notna().sum()),
        "n_positive_volume": len(matched),
        "n_zero_btc_volume": int((frame["match_tier"] == "zero_btc_volume").sum()),
        "n_no_au_match": int((frame["match_tier"] == "no_au_match").sum()),
        "n_matched": len(matched),
        "n_unmatched": len(unmatched),
        "match_tier_counts": {
            str(key): int(value)
            for key, value in frame["match_tier"].value_counts().items()
        },
        "observed_all_vs_btc": _metrics(
            matched["observed_merchantable_all_m3ha"].tolist(),
            matched["btc_volume_m3ha"].tolist(),
        ),
        "bucked_ratio_est_vs_btc": _metrics(
            matched["pilot_bucked_log_m3ha_ratio_est"].tolist(),
            matched["btc_volume_m3ha"].tolist(),
        ),
        "bucked_sampled_vs_btc": _metrics(
            matched["pilot_bucked_log_m3ha"].tolist(),
            matched["btc_volume_m3ha"].tolist(),
        ),
        "matched_plots": matched["plot_id"].astype(str).tolist(),
        "unmatched_plots": unmatched[
            ["plot_id", "bec_zone", "leading_species", "stand_age", "match_tier", "match_reason"]
        ].to_dict(orient="records"),
        "join_basis": (
            "FAIB plot -> BEC + leading species -> managed FEMIC AU stratum "
            "(including combined-species strata) -> FEMIC AU site-index class -> "
            "managed AU; BTC /TSR volume interpolated at FAIB stand age. "
            "This is the same benchmark join as the grade table, not an "
            "operational AU assignment."
        ),
        "caveat": (
            "The comparison is a benchmark, not a validation gate. The FAIB "
            "stand age and the managed AU age origin are not guaranteed to be "
            "the same clock; young plots can show near-zero BTC volume and are "
            "reported as unmatched by volume rather than silently clipped."
        ),
    }
    return summary, frame


def _prepare_log_biomass(
    logs: pd.DataFrame,
    tree_summary: pd.DataFrame,
    density_table: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    required_logs = {"tree_id", "plot_id", "volume_m3"}
    missing_logs = sorted(required_logs.difference(logs.columns))
    if missing_logs:
        raise ValueError(f"bucked logs missing columns: {', '.join(missing_logs)}")
    required_trees = {"tree_id", "species", "expansion_factor"}
    missing_trees = sorted(required_trees.difference(tree_summary.columns))
    if missing_trees:
        raise ValueError(f"tree summary missing columns: {', '.join(missing_trees)}")

    tree_columns = tree_summary[["tree_id", "species", "expansion_factor"]].rename(
        columns={
            "species": "tree_species",
            "expansion_factor": "tree_expansion_factor",
        }
    )
    merged = logs.merge(tree_columns, on="tree_id", how="left", validate="many_to_one")
    if (
        merged["tree_species"].isna().any()
        or merged["tree_expansion_factor"].isna().any()
    ):
        missing = merged[merged["tree_species"].isna()]["tree_id"].astype(str).tolist()
        raise ValueError(f"bucked logs could not be joined to tree summary: {missing[:5]}")

    merged["basic_density_odt_m3"] = [
        resolve_species_density(species, density_table)
        for species in merged["tree_species"]
    ]
    merged["log_odt_ha"] = (
        merged["volume_m3"].astype(float)
        * merged["tree_expansion_factor"].astype(float)
        * merged["basic_density_odt_m3"]
    )
    merged["log_m3_ha"] = (
        merged["volume_m3"].astype(float)
        * merged["tree_expansion_factor"].astype(float)
    )
    return merged


def _tree_residue_summary(tree_summary: pd.DataFrame) -> pd.DataFrame:
    required = {"plot_id", "residue_odt_ha", "residue_m3_ha", "taper_total_m3_ha"}
    missing = sorted(required.difference(tree_summary.columns))
    if missing:
        raise ValueError(f"tree summary missing residue columns: {', '.join(missing)}")
    return (
        tree_summary.groupby("plot_id", dropna=False)
        .agg(
            taper_residue_odt_ha_sampled=("residue_odt_ha", "sum"),
            taper_residue_m3ha_sampled=("residue_m3_ha", "sum"),
            taper_total_m3ha_sampled=("taper_total_m3_ha", "sum"),
        )
        .reset_index()
    )


def _residue_status(
    *,
    au_id: int | None,
    age: float,
    btc_volume: float,
    agb: float,
    observed_all: float,
    observed_sampled: float,
    merchantable_log_odt: float,
    merchantable_log_m3: float,
    volume_ratio_min: float,
    volume_ratio_max: float,
) -> tuple[str, str | None]:
    if au_id is None:
        return "no_au_match", "no managed FEMIC AU for BEC + leading species + SI class"
    if not math.isfinite(age):
        return "no_age", "FAIB stand age missing"
    if not math.isfinite(btc_volume) or btc_volume <= 0:
        return "zero_btc_volume", "BTC volume is zero or non-finite at the plot age"
    if not math.isfinite(agb):
        return "no_biomass_curve", "no compiled biomass curve at the matched AU and age"
    if observed_sampled <= 0:
        return "no_observed_sample", "no observed merchantable volume on the sampled trees"
    ratio = observed_all / btc_volume
    if not volume_ratio_min <= ratio <= volume_ratio_max:
        return (
            "volume_ratio_out_of_range",
            (
                f"observed/BTC volume ratio {ratio:.3f} outside "
                f"{volume_ratio_min:.2f}-{volume_ratio_max:.2f}"
            ),
        )
    if merchantable_log_m3 <= 0:
        return "no_logs", "pilot bucked no merchantable logs"
    if agb <= merchantable_log_odt:
        return (
            "agb_below_logs",
            (
                f"AGB {agb:.3f} ODT/ha is not above merchantable log biomass "
                f"{merchantable_log_odt:.3f} ODT/ha"
            ),
        )
    return "ok", None


def harvest_residue_ledger(
    *,
    attrs: pd.DataFrame,
    plot_summary: pd.DataFrame,
    tree_summary: pd.DataFrame,
    logs: pd.DataFrame,
    compiled_dir: Path,
    density_table: Mapping[str, Mapping[str, Any]],
    recovery_factor: float = DEFAULT_RECOVERY_FACTOR,
    moisture_green: float = DEFAULT_MOISTURE_GREEN,
    moisture_seasoned: float = DEFAULT_MOISTURE_SEASONED,
    chip_yield: float = DEFAULT_CHIP_YIELD,
    volume_ratio_min: float = DEFAULT_VOLUME_RATIO_MIN,
    volume_ratio_max: float = DEFAULT_VOLUME_RATIO_MAX,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Build a plot-level harvest-slash ledger from AGB and pilot logs.

    ``harvest_residue_odt = agb_odt - merchantable_log_biomass_odt``.  AGB is
    the BTC stand-level ``Biomass_Live_Above`` value at the matched AU and
    FAIB stand age.  It is allocated to the plot on a per-hectare basis and
    scaled by the plot's observed/BTC merchantable-volume ratio; no per-tree
    AGB is invented.  Merchantable log biomass is the sum of bucked log
    volume x species basic density, expanded from the sampled trees to the
    full live supported tree population by the observed merchantable-volume
    ratio.

    Plots whose observed/BTC volume ratio is outside a documented band, or
    whose AGB does not exceed the log biomass, are retained in the output but
    excluded from the primary ledger.  That prevents a bad age join from
    being reported as negative slash.
    """
    if not 0 < recovery_factor <= 1:
        raise ValueError("recovery_factor must be in (0, 1]")
    if not 0 <= moisture_green < 1 or not 0 <= moisture_seasoned < 1:
        raise ValueError("moisture fractions must be in [0, 1)")
    if not 0 < chip_yield <= 1:
        raise ValueError("chip_yield must be in (0, 1]")
    if not 0 < volume_ratio_min < volume_ratio_max:
        raise ValueError("volume ratio bounds must satisfy 0 < min < max")

    curves, _coverage = _load_btc_curves(compiled_dir)
    volume_by_au = _curve_lookup(curves, "au_id", "au_id")
    biomass_path = Path(compiled_dir) / "biomass_au_curves.csv"
    if not biomass_path.exists():
        raise FileNotFoundError(f"compiled biomass curves are missing: {biomass_path}")
    biomass = pd.read_csv(biomass_path)
    required_biomass = {
        "au_id",
        "age",
        "agb_odt_ha",
        "biomass_live_wood_odt_ha",
        "biomass_live_bark_odt_ha",
        "biomass_live_foliar_odt_ha",
        "biomass_live_branch_odt_ha",
    }
    missing_biomass = sorted(required_biomass.difference(biomass.columns))
    if missing_biomass:
        raise ValueError(
            f"biomass_au_curves.csv missing columns: {', '.join(missing_biomass)}"
        )
    biomass_by_au = _curve_lookup(biomass, "au_id", "au_id")

    log_biomass = _prepare_log_biomass(logs, tree_summary, density_table)
    log_by_plot = (
        log_biomass.groupby("plot_id", dropna=False)
        .agg(
            log_odt_ha_sampled=("log_odt_ha", "sum"),
            log_m3_ha_sampled=("log_m3_ha", "sum"),
        )
        .reset_index()
    )
    taper_by_plot = _tree_residue_summary(tree_summary)

    merged = (
        plot_summary.merge(log_by_plot, on="plot_id", how="left")
        .merge(taper_by_plot, on="plot_id", how="left")
        .fillna(
            {
                "log_odt_ha_sampled": 0.0,
                "log_m3_ha_sampled": 0.0,
                "taper_residue_odt_ha_sampled": 0.0,
                "taper_residue_m3ha_sampled": 0.0,
                "taper_total_m3ha_sampled": 0.0,
            }
        )
    )

    rows: list[dict[str, Any]] = []
    for plot in merged.itertuples(index=False):
        plot_id = str(plot.plot_id)
        attribute = _plot_attribute(attrs, plot_id)
        au_value = attribute.get("au_id") if attribute else None
        au_id = int(au_value) if au_value is not None and pd.notna(au_value) else None
        age = float(plot.stand_age) if pd.notna(plot.stand_age) else float("nan")
        stratum = (
            str(attribute["stratum_code"])
            if attribute and pd.notna(attribute.get("stratum_code"))
            else None
        )
        si_level = (
            str(attribute["si_level"])
            if attribute and pd.notna(attribute.get("si_level"))
            else None
        )
        btc_volume = (
            _finite_interp(age, volume_by_au[au_id], "volume")
            if au_id is not None and math.isfinite(age) and au_id in volume_by_au
            else float("nan")
        )
        biomass_row: Mapping[str, float] = {}
        if (
            au_id is not None
            and math.isfinite(age)
            and au_id in biomass_by_au
        ):
            biomass_row = {
                column: _finite_interp(age, biomass_by_au[au_id], column)
                for column in (
                    "agb_odt_ha",
                    "biomass_live_wood_odt_ha",
                    "biomass_live_bark_odt_ha",
                    "biomass_live_foliar_odt_ha",
                    "biomass_live_branch_odt_ha",
                )
            }
        agb_unscaled = float(biomass_row.get("agb_odt_ha", float("nan")))
        observed_all = float(plot.observed_merchantable_all_m3ha)
        observed_sampled = float(plot.observed_merchantable_sampled_m3ha)
        volume_scale = (
            observed_all / btc_volume
            if math.isfinite(btc_volume) and btc_volume > 0
            else float("nan")
        )
        agb = agb_unscaled * volume_scale
        expansion = (
            observed_all / observed_sampled if observed_sampled > 0 else float("nan")
        )
        merchantable_log_odt = float(plot.log_odt_ha_sampled) * expansion
        merchantable_log_m3 = float(plot.log_m3_ha_sampled) * expansion
        status, reason = _residue_status(
            au_id=au_id,
            age=age,
            btc_volume=btc_volume,
            agb=agb,
            observed_all=observed_all,
            observed_sampled=observed_sampled,
            merchantable_log_odt=merchantable_log_odt,
            merchantable_log_m3=merchantable_log_m3,
            volume_ratio_min=volume_ratio_min,
            volume_ratio_max=volume_ratio_max,
        )
        ledger_has_values = status in {"ok", "agb_below_logs"}
        agb_ledger = agb if ledger_has_values else float("nan")
        merchantable_log_odt_ledger = (
            merchantable_log_odt if ledger_has_values else float("nan")
        )
        merchantable_log_m3_ledger = (
            merchantable_log_m3 if ledger_has_values else float("nan")
        )
        taper_residue_odt = float(plot.taper_residue_odt_ha_sampled) * expansion
        taper_residue_m3 = float(plot.taper_residue_m3ha_sampled) * expansion
        taper_total_m3 = float(plot.taper_total_m3ha_sampled) * expansion
        harvest_residue = agb_ledger - merchantable_log_odt_ledger
        harvest_residue_unscaled = (
            agb_unscaled - merchantable_log_odt_ledger
            if ledger_has_values
            else float("nan")
        )
        non_taper_residue = harvest_residue - taper_residue_odt
        bark = float(biomass_row.get("biomass_live_bark_odt_ha", float("nan")))
        foliar = float(biomass_row.get("biomass_live_foliar_odt_ha", float("nan")))
        branch = float(biomass_row.get("biomass_live_branch_odt_ha", float("nan")))
        wood = float(biomass_row.get("biomass_live_wood_odt_ha", float("nan")))
        bark_scaled = bark * volume_scale if ledger_has_values else float("nan")
        foliar_scaled = foliar * volume_scale if ledger_has_values else float("nan")
        branch_scaled = branch * volume_scale if ledger_has_values else float("nan")
        wood_scaled = wood * volume_scale if ledger_has_values else float("nan")
        branch_foliage_bark = bark_scaled + foliar_scaled + branch_scaled
        unmerchantable_wood = wood_scaled - merchantable_log_odt_ledger
        recoverable = harvest_residue * recovery_factor
        green_45 = harvest_residue / (1.0 - moisture_green)
        green_30 = harvest_residue / (1.0 - moisture_seasoned)
        recoverable_green_45 = recoverable / (1.0 - moisture_green)
        recoverable_green_30 = recoverable / (1.0 - moisture_seasoned)
        recoverable_chip = recoverable_green_45 * chip_yield
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
                "status": status,
                "status_reason": reason,
                "observed_merchantable_all_m3ha": observed_all,
                "observed_merchantable_sampled_m3ha": observed_sampled,
                "btc_volume_m3ha": btc_volume,
                "observed_to_btc_volume_ratio": (
                    observed_all / btc_volume
                    if math.isfinite(btc_volume) and btc_volume > 0
                    else float("nan")
                ),
                "agb_volume_scale": volume_scale,
                "tree_to_plot_expansion": expansion,
                "agb_odt_ha": agb_ledger,
                "agb_odt_ha_unscaled": agb_unscaled,
                "merchantable_log_odt_ha": merchantable_log_odt_ledger,
                "merchantable_log_m3ha": merchantable_log_m3_ledger,
                "harvest_residue_odt_ha": harvest_residue,
                "harvest_residue_odt_ha_unscaled": harvest_residue_unscaled,
                "taper_residue_odt_ha": taper_residue_odt,
                "taper_residue_m3ha": taper_residue_m3,
                "taper_total_m3ha": taper_total_m3,
                "non_taper_residue_odt_ha": non_taper_residue,
                "biomass_live_wood_odt_ha": wood_scaled,
                "biomass_live_bark_odt_ha": bark_scaled,
                "biomass_live_foliar_odt_ha": foliar_scaled,
                "biomass_live_branch_odt_ha": branch_scaled,
                "branch_foliage_bark_odt_ha": branch_foliage_bark,
                "unmerchantable_wood_odt_ha": unmerchantable_wood,
                "harvest_residue_odt_per_m3_log": (
                    harvest_residue / merchantable_log_m3_ledger
                    if merchantable_log_m3_ledger > 0
                    else float("nan")
                ),
                "taper_residue_odt_per_m3_taper_total": (
                    taper_residue_odt / taper_total_m3 if taper_total_m3 > 0 else float("nan")
                ),
                "recovery_factor": recovery_factor,
                "recoverable_residue_odt_ha": recoverable,
                "green_tonnes_45_ha": green_45,
                "green_tonnes_30_ha": green_30,
                "recoverable_green_tonnes_45_ha": recoverable_green_45,
                "recoverable_green_tonnes_30_ha": recoverable_green_30,
                "recoverable_chip_green_tonnes_ha": recoverable_chip,
                "moisture_green_wet_basis": moisture_green,
                "moisture_seasoned_wet_basis": moisture_seasoned,
                "chip_yield_fraction": chip_yield,
            }
        )

    frame = pd.DataFrame(rows).sort_values("plot_id").reset_index(drop=True)
    primary = frame[frame["status"] == "ok"].copy()
    status_counts = {
        str(key): int(value) for key, value in frame["status"].value_counts().items()
    }
    excluded = frame[frame["status"] != "ok"][
        ["plot_id", "bec_zone", "leading_species", "stand_age", "status", "status_reason"]
    ].to_dict(orient="records")

    summary = {
        "status": "reported_with_mass_balance_screen" if not primary.empty else "no_valid_plots",
        "n_plots_total": len(frame),
        "n_plots_primary": len(primary),
        "n_plots_excluded": int(len(frame) - len(primary)),
        "status_counts": status_counts,
        "primary_plot_ids": primary["plot_id"].astype(str).tolist(),
        "excluded_plots": excluded,
        "per_ha": {
            "agb_odt_ha": _summary_stats(primary["agb_odt_ha"].tolist()),
            "agb_odt_ha_unscaled": _summary_stats(
                primary["agb_odt_ha_unscaled"].tolist()
            ),
            "merchantable_log_odt_ha": _summary_stats(
                primary["merchantable_log_odt_ha"].tolist()
            ),
            "harvest_residue_odt_ha": _summary_stats(
                primary["harvest_residue_odt_ha"].tolist()
            ),
            "harvest_residue_odt_ha_unscaled": _summary_stats(
                primary["harvest_residue_odt_ha_unscaled"].tolist()
            ),
            "taper_residue_odt_ha": _summary_stats(
                primary["taper_residue_odt_ha"].tolist()
            ),
            "non_taper_residue_odt_ha": _summary_stats(
                primary["non_taper_residue_odt_ha"].tolist()
            ),
            "branch_foliage_bark_odt_ha": _summary_stats(
                primary["branch_foliage_bark_odt_ha"].tolist()
            ),
            "unmerchantable_wood_odt_ha": _summary_stats(
                primary["unmerchantable_wood_odt_ha"].tolist()
            ),
            "recoverable_residue_odt_ha": _summary_stats(
                primary["recoverable_residue_odt_ha"].tolist()
            ),
            "green_tonnes_45_ha": _summary_stats(
                primary["green_tonnes_45_ha"].tolist()
            ),
            "green_tonnes_30_ha": _summary_stats(
                primary["green_tonnes_30_ha"].tolist()
            ),
            "recoverable_green_tonnes_45_ha": _summary_stats(
                primary["recoverable_green_tonnes_45_ha"].tolist()
            ),
            "recoverable_chip_green_tonnes_ha": _summary_stats(
                primary["recoverable_chip_green_tonnes_ha"].tolist()
            ),
        },
        "per_m3": {
            "harvest_residue_odt_per_m3_log": _summary_stats(
                primary["harvest_residue_odt_per_m3_log"].tolist()
            ),
            "taper_residue_odt_per_m3_taper_total": _summary_stats(
                primary["taper_residue_odt_per_m3_taper_total"].tolist()
            ),
        },
        "totals_over_primary_plot_ha": {
            "sample_hectares": len(primary),
            "agb_odt": float(primary["agb_odt_ha"].sum()),
            "merchantable_log_odt": float(primary["merchantable_log_odt_ha"].sum()),
            "harvest_residue_odt": float(primary["harvest_residue_odt_ha"].sum()),
            "taper_residue_odt": float(primary["taper_residue_odt_ha"].sum()),
            "non_taper_residue_odt": float(primary["non_taper_residue_odt_ha"].sum()),
            "recoverable_residue_odt": float(
                primary["recoverable_residue_odt_ha"].sum()
            ),
            "recoverable_green_tonnes_45": float(
                primary["recoverable_green_tonnes_45_ha"].sum()
            ),
            "recoverable_chip_green_tonnes": float(
                primary["recoverable_chip_green_tonnes_ha"].sum()
            ),
        },
        "ratio_harvest_to_taper_residue": (
            float(primary["harvest_residue_odt_ha"].sum() / primary["taper_residue_odt_ha"].sum())
            if primary["taper_residue_odt_ha"].sum() > 0
            else None
        ),
        "assumptions": {
            "definition": (
                "harvest_residue_odt = AGB_ODT - merchantable_log_biomass_ODT; "
                "merchantable_log_biomass_ODT = sum(log_volume_m3 * basic_density_odt_m3)"
            ),
            "agb_source": (
                "BTC biomass-live bank, Biomass_Live_Above at the matched managed "
                "AU and FAIB stand age, ODT/ha. The stand-level value is reported "
                "unscaled as agb_odt_ha_unscaled and volume-scaled for the ledger."
            ),
            "allocation": (
                "No per-tree AGB is invented. The stand-level AGB is applied to the "
                "plot on a per-hectare basis and scaled by observed_merchantable_all "
                "/ BTC_volume so the AGB and the pilot log volume are on the same "
                "plot productivity basis. Merchantable log biomass is expanded from "
                "sampled trees by PHF_TREE and then by observed all-tree/sampled-tree "
                "merchantable volume. The unscaled AGB is retained for sensitivity."
            ),
            "basic_density": (
                "Species-specific defaults from rules/bc_basic_density.csv "
                "(FVS-BC NBEL/FIA table; not a BC-local sample)."
            ),
            "recovery_factor": recovery_factor,
            "moisture_green_wet_basis": moisture_green,
            "moisture_seasoned_wet_basis": moisture_seasoned,
            "chip_yield_fraction": chip_yield,
            "volume_ratio_screen": {
                "min": volume_ratio_min,
                "max": volume_ratio_max,
                "note": (
                    "Plots outside the observed/BTC volume band are kept in the "
                    "ledger output but excluded from the primary summary because "
                    "the AU/age join is not mass-balance defensible."
                ),
            },
            "mill_residue_not_used": (
                "BTC Residual_Chips/Sawdust/Shavings/Trim/Bark are mill-processing "
                "residues and are not harvest slash; they are deliberately excluded."
            ),
        },
        "caveat": (
            "AGB is a stand-level model output, not a plot measurement. The "
            "difference between AGB-based slash and taper residue is expected: "
            "AGB includes bark, branches, and foliage, while the taper ledger "
            "only covers top, stump, and unmerchantable stem sections."
        ),
    }
    return summary, frame
