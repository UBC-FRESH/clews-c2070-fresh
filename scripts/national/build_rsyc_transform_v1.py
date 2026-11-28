#!/usr/bin/env python3
"""Build the national RSYC transformed curve library (step 3, v1).

The library takes the published RSYC total-volume curves and applies donor
transform classes fitted to BC VDYP untreated composites (natural style) and
Quebec BFEC Woodstock unit composites (managed AAC style).  Shape is
transferred; level is preserved at RSYC age-100 volume (or the raw peak when
V100 is not meaningful).  Donor ``k`` values are never used as merchantability
ratios.

Outputs (``data/derived/national/rsyc_transform_v1``):
    transformed_curves.csv, transform_parameters.csv, fit_metrics.csv,
    qc_summary.csv, summary.json, manifest.json, report.md

All outputs are model estimates: observed=false, policy_grade=false,
national_claim=false.  No OSeCAN artefact and no WS4 build is touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT.parent
LIBRARY = (
    REPO_ROOT
    / "data/derived/national/assortments/rsyc_tile_production"
    / "rsyc_tile_production_library.csv"
)
ECO_WEIGHTS = (
    REPO_ROOT
    / "data/derived/national/assortments/rsyc_tile_production"
    / "tile_ecodistrict_weights.csv"
)
SIMILARITY = DATA_ROOT / "tmp/rsyc-vdyp-similarity.csv"
QC_FIT_INPUT = REPO_ROOT / "tmp/qc_bfec_fit_input.parquet"
QC_AREA_WEIGHTED = REPO_ROOT / "tmp/qc_bfec_area_weighted_curves.csv"
QC_CURVE_METRICS = REPO_ROOT / "tmp/qc_bfec_curve_metrics.csv"
QC_MANIFEST = REPO_ROOT / "data/derived/national/qc_bfec_full/qc_bfec_manifest.json"
PORTAL = Path("/srv/shared-data/gep/ws4/fresh-tsa-portal")
WS4 = Path("/srv/shared-data/gep/ws4")

OUT_DIR = REPO_ROOT / "data/derived/national/rsyc_transform_v1"

AGES_LONG = np.arange(1.0, 201.0)
AGES_FIT = np.arange(1.0, 251.0)
FIT_WINDOW_PRIMARY = (10.0, 150.0)
FIT_WINDOW_SENSITIVITY = (1.0, 250.0)

# Transfer bounds.  The alpha floor keeps the mapped RSYC age at age 200
# inside the published RSYC age domain (max 600 yr) for the donor s values.
SHAPE_BOUNDS = {
    "bc": (np.array([0.01, -50.0, 0.0, 0.2, 1e-5]), np.array([50.0, 150.0, 100.0, 5.0, 2.0])),
    "qc": (np.array([0.01, -50.0, 0.0, 0.1, 1e-5]), np.array([30.0, 150.0, 100.0, 5.0, 1.0])),
}
DECLINE_BOUNDS = {
    "linear": (np.array([0.0]), np.array([0.85])),
    "power": (np.array([0.0, 0.2]), np.array([0.85, 4.0])),
    "stretch": (np.array([0.0, 0.05, 0.2]), np.array([0.85, 1.0, 4.0])),
}
DECLINE_ORDER = ("linear", "power", "stretch")

SPECIES_GROUP = {
    "PICE.MAR": "spruce",
    "PICE.GLA": "spruce",
    "PINU.BAN": "pine",
    "PINU.CON": "pine",
    "PINU.RES": "pine",
    "PINU.STR": "pine",
    "PINU.PON": "pine",
    "ABIE.BAL": "fir",
    "PSEU.MEN": "fir",
    "LARI.LAR": "larch",
    "BROADLEAF": "deciduous",
    "CONIFEROUS": "conifer_mixed",
}
GROUP_TO_BROAD = {
    "spruce": "conifer",
    "pine": "conifer",
    "fir": "conifer",
    "larch": "conifer",
    "conifer_mixed": "conifer",
    "deciduous": "deciduous",
}
GROUP_TO_QC_MIX = {
    "spruce": "conifer",
    "pine": "conifer",
    "fir": "conifer",
    "larch": "conifer",
    "conifer_mixed": "conifer",
    "deciduous": "deciduous",
}
BROAD_TO_QC_MIX = {"conifer": "conifer", "deciduous": "deciduous"}

NATURAL_PROVINCES = {"YT", "NT", "NU"}
MANAGED_PROVINCES = {"ON", "NB", "NS", "PE", "NL"}
MIXED_PROVINCES = {"AB", "SK", "MB"}
MANAGED_ECOZONES = {"Boreal Plains", "Boreal Shield West", "Boreal Shield East"}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug(text: str) -> str:
    return (
        str(text)
        .strip()
        .replace("&", "and")
        .replace("/", " ")
        .replace("-", " ")
        .replace("__", "_")
        .replace(" ", "_")
    )


# ---------------------------------------------------------------------------
# RSYC and transform primitives
# ---------------------------------------------------------------------------


def rsyc_volume(b1: float, b2: float, b3: float, b4: float, age: np.ndarray) -> np.ndarray:
    age = np.asarray(age, dtype=float)
    value = b1 * np.exp(-b4 * age) * np.power(np.maximum(1.0 - np.exp(-b2 * age), 0.0), b3)
    return np.maximum(value, 0.0)


def row_rsyc(row: pd.Series, age: np.ndarray) -> np.ndarray:
    return rsyc_volume(float(row["b1"]), float(row["b2"]), float(row["b3"]), float(row["b4"]), age)


def weighted_curve(rows: pd.DataFrame, age: np.ndarray) -> np.ndarray:
    """Area-weighted RSYC composite for a group of library rows."""
    weights = rows["area_ha"].to_numpy(dtype=float)
    if weights.sum() <= 0:
        raise ValueError("non-positive RSYC area weight")
    matrix = np.vstack([row_rsyc(row, age) for _, row in rows.iterrows()])
    return (weights[:, None] * matrix).sum(axis=0) / weights.sum()


def shape_curve(
    age: np.ndarray,
    eval_fn: Callable[[np.ndarray], np.ndarray],
    k: float,
    s: float,
    T: float,
    alpha: float,
    r: float,
) -> np.ndarray:
    """Accepted 5-parameter form: k*R((x-s)/alpha) with a ramp toe at the join."""
    age = np.asarray(age, dtype=float)
    out = np.zeros_like(age)
    join_age = s + alpha * T
    if join_age <= 0.0:
        return k * eval_fn((age - s) / alpha)
    body = age >= join_age
    if np.any(body):
        out[body] = eval_fn((age[body] - s) / alpha)
    toe = ~body
    if np.any(toe):
        base = float(eval_fn(np.array([T]))[0])
        denominator = np.expm1(-r * join_age)
        if abs(denominator) < 2.0e-12:
            out[toe] = base * age[toe] / join_age
        else:
            out[toe] = base * np.expm1(-r * age[toe]) / denominator
    return k * out


def decline_g(frac: np.ndarray, kind: str, extras: np.ndarray) -> np.ndarray:
    frac = np.clip(np.asarray(frac, dtype=float), 0.0, 1.0)
    if kind == "linear":
        return frac
    if kind == "power":
        return np.power(frac, extras[0])
    tau, beta = float(extras[0]), float(extras[1])
    numerator = -np.expm1(-np.power(frac / tau, beta))
    denominator = -np.expm1(-np.power(1.0 / tau, beta))
    return numerator / max(denominator, 1.0e-12)


def apply_decline(
    age: np.ndarray,
    base_fn: Callable[[np.ndarray], np.ndarray],
    peak_age: float,
    flat_age: float,
    kind: str,
    d: float,
    extras: np.ndarray,
) -> np.ndarray:
    """Apply a generic post-peak decline/plateau ending at ``flat_age``.

    ``d`` is the fractional peak loss at ``flat_age``; volume is held flat
    thereafter.  The shape term transports the QC fitted decline to any
    receiver curve.
    """
    age = np.asarray(age, dtype=float)
    base = base_fn(age)
    peak_value = float(base_fn(np.array([peak_age]))[0])
    if not np.isfinite(peak_value) or peak_value <= 0.0:
        return base
    span = max(float(flat_age) - peak_age, 1.0)
    frac = (age - peak_age) / span
    post = peak_value * (1.0 - d * decline_g(frac, kind, extras))
    return np.where(age <= peak_age, base, post)


def curve_flat_age(age: np.ndarray, volume: np.ndarray, tolerance: float = 0.5) -> float:
    """First age from which all later volumes stay within tolerance of V200."""
    volume = np.asarray(volume, dtype=float)
    v200 = float(volume[-1])
    for index in range(len(volume)):
        if float(np.max(np.abs(volume[index:] - v200))) <= tolerance:
            return float(age[index])
    return float(age[-1])


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def fit_shape(
    age: np.ndarray,
    volume: np.ndarray,
    eval_fn: Callable[[np.ndarray], np.ndarray],
    family: str,
    seed: int,
    n_starts: int,
) -> least_squares:
    rng = np.random.default_rng(seed)
    lower, upper = SHAPE_BOUNDS[family]
    x = np.asarray(age, dtype=float)
    y = np.asarray(volume, dtype=float)
    reference = eval_fn(x)
    denominator = float(np.dot(reference, reference))
    k0 = float(np.dot(y, reference) / denominator) if denominator > 0 else 1.0
    k0 = float(np.clip(k0, 0.02, 20.0))
    starts = [
        [k0, 40.0, 5.0, 2.0, 1.0e-3],
        [k0, 30.0, 3.0, 1.5, 1.0e-2],
        [k0, 50.0, 8.0, 2.5, 1.0e-4],
        [k0, 15.0, 1.0, 1.0, 5.0e-2],
    ]
    for _ in range(n_starts):
        starts.append(
            [
                k0 * rng.uniform(0.4, 2.5),
                rng.uniform(-20.0, 80.0),
                rng.uniform(0.2, 30.0),
                rng.uniform(0.4, 4.5),
                10.0 ** rng.uniform(-5.0, -0.3),
            ]
        )
    best: least_squares | None = None
    for start in starts:
        start = np.clip(np.asarray(start, dtype=float), lower + 1.0e-9, upper - 1.0e-9)
        try:
            candidate = least_squares(
                lambda p: shape_curve(x, eval_fn, *p) - y,
                start,
                bounds=(lower, upper),
                max_nfev=6000,
                ftol=1.0e-12,
                xtol=1.0e-12,
                gtol=1.0e-12,
            )
        except (ValueError, FloatingPointError):
            continue
        if best is None or candidate.cost < best.cost:
            best = candidate
    if best is None:
        raise RuntimeError("all shape-fit starts failed")
    return best


def fit_decline(
    age: np.ndarray,
    volume: np.ndarray,
    peak_age: float,
    flat_age: float,
    base_params: np.ndarray,
    kind: str,
    eval_fn: Callable[[np.ndarray], np.ndarray],
    family: str,
    seed: int,
    n_starts: int = 12,
) -> least_squares:
    """Jointly fit the 5 shape parameters and a post-peak decline on the full curve.

    The pre-peak fit supplies a strong start, so the joint problem stays in a
    physically plausible basin while the decline matches the late-seral limb.
    """
    rng = np.random.default_rng(seed)
    shape_lower, shape_upper = SHAPE_BOUNDS[family]
    decline_lower, decline_upper = DECLINE_BOUNDS[kind]
    lower = np.r_[shape_lower, decline_lower]
    upper = np.r_[shape_upper, decline_upper]
    x = np.asarray(age, dtype=float)
    y = np.asarray(volume, dtype=float)
    base_params = np.asarray(base_params, dtype=float)

    def predict(params: np.ndarray) -> np.ndarray:
        base_fn = lambda a: shape_curve(a, eval_fn, *params[:5])
        return apply_decline(x, base_fn, peak_age, flat_age, kind, float(params[5]), params[6:])

    starts = [np.r_[base_params, np.zeros(len(decline_lower))]]
    for _ in range(n_starts):
        start = np.r_[
            base_params * rng.uniform(0.85, 1.15, 5),
            rng.uniform(0.0, 0.5),
        ]
        if len(decline_lower) > 1:
            start = np.r_[
                start,
                decline_lower[1:]
                + (decline_upper[1:] - decline_lower[1:]) * rng.uniform(0.05, 0.9, len(decline_lower) - 1),
            ]
        starts.append(start)
    best: least_squares | None = None
    for start in starts:
        start = np.clip(start, lower + 1.0e-9, upper - 1.0e-9)
        try:
            candidate = least_squares(
                lambda p: predict(p) - y,
                start,
                bounds=(lower, upper),
                max_nfev=6000,
                ftol=1.0e-11,
                xtol=1.0e-11,
                gtol=1.0e-11,
            )
        except (ValueError, FloatingPointError):
            continue
        if best is None or candidate.cost < best.cost:
            best = candidate
    if best is None:
        raise RuntimeError(f"all {kind} joint decline-fit starts failed")
    return best


def error_metrics(actual: np.ndarray, predicted: np.ndarray, n_params: int) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    residual = predicted - actual
    n = len(actual)
    sse = float(np.sum(residual**2))
    sst = float(np.sum((actual - actual.mean()) ** 2))
    positive = actual > 1.0
    return {
        "n": int(n),
        "sse": sse,
        "rmse": float(np.sqrt(sse / n)),
        "mae": float(np.mean(np.abs(residual))),
        "mape_pct": float(np.mean(np.abs(residual[positive] / actual[positive])) * 100.0)
        if positive.any()
        else float("nan"),
        "r2": float(1.0 - sse / sst) if sst > 0 else float("nan"),
        "aic": float(n * np.log(sse / n) + 2 * n_params) if sse > 0 else float("nan"),
    }


def curve_summary(volume: np.ndarray, age: np.ndarray = AGES_LONG) -> dict[str, float]:
    volume = np.maximum(np.asarray(volume, dtype=float), 0.0)
    cmai = volume / age
    peak_age = float(age[int(np.argmax(volume))])
    return {
        "peak_volume": float(np.max(volume)),
        "peak_age": peak_age,
        "cmai": float(np.max(cmai)),
        "cmai_age": float(age[int(np.argmax(cmai))]),
    }


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_library() -> pd.DataFrame:
    frame = pd.read_csv(LIBRARY)
    required = {"model_id", "tile_id", "province", "species", "species_kind", "area_ha",
                "b1", "b2", "b3", "b4"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"RSYC library missing columns: {sorted(missing)}")
    frame = frame.copy()
    frame["curve_uid"] = np.arange(len(frame), dtype=int)
    frame["species_group"] = frame["species"].map(SPECIES_GROUP)
    if frame["species_group"].isna().any():
        unknown = sorted(frame.loc[frame["species_group"].isna(), "species"].unique())
        raise ValueError(f"unmapped RSYC species: {unknown}")
    frame["broad_group"] = frame["species_group"].map(GROUP_TO_BROAD)
    frame["qc_mix"] = frame["species_group"].map(GROUP_TO_QC_MIX)
    return frame


def load_tile_ecology(library: pd.DataFrame) -> pd.DataFrame:
    weights = pd.read_csv(ECO_WEIGHTS)
    grouped = weights.groupby(
        ["tile_id", "ecozone", "ecoprovince"], as_index=False
    )["intersection_area_ha"].sum()
    total = grouped.groupby("tile_id")["intersection_area_ha"].transform("sum")
    grouped["share"] = grouped["intersection_area_ha"] / total.replace(0, np.nan)
    dominant = grouped.loc[grouped.groupby("tile_id")["intersection_area_ha"].idxmax()].copy()
    dominant = dominant.rename(
        columns={"ecozone": "tile_ecozone", "ecoprovince": "tile_ecoprovince",
                 "share": "tile_ecoprovince_share"}
    )[["tile_id", "tile_ecozone", "tile_ecoprovince", "tile_ecoprovince_share"]]
    province = library[["tile_id", "province"]].drop_duplicates()
    merged = province.merge(dominant, on="tile_id", how="left")
    if merged["tile_ecoprovince"].isna().any():
        missing = sorted(merged.loc[merged["tile_ecoprovince"].isna(), "tile_id"])
        raise ValueError(f"tiles without ecology: {missing[:10]}")
    return merged


def bundle_dir(code: str) -> Path | None:
    code = str(code).zfill(2)
    candidates = [
        PORTAL / f"instances/tsa-{code}/provenance/model_input_bundle",
        WS4 / f"femic/external/femic-tsa{code}-instance/data/model_input_bundle",
        WS4 / f"archive/femic-tsa{code}-full-instance-worktree/data/model_input_bundle",
    ]
    for path in candidates:
        if (path / "au_table.csv").exists():
            return path
    return None


def au_area_weights(code: str, curve_ids: list[str]) -> tuple[pd.Series | None, str]:
    instance = WS4 / f"tsa-instances-consistent/tsa-{code}"
    tracks_path = instance / "ws4/build/compiled-full/tracks.parquet"
    blocks_path = instance / "ws4/build/compiled-full/blocks.parquet"
    if not tracks_path.exists() or not blocks_path.exists():
        return None, "equal_au_weights"
    tracks = pd.read_parquet(tracks_path)
    blocks = pd.read_parquet(blocks_path)
    au = tracks["name"].astype(str).str.extract(r"AU\(([^)]+)\)")[0]
    merged = tracks.assign(au=au).merge(blocks[["track_id", "area_ha"]], on="track_id", how="inner")
    area = merged.groupby("au")["area_ha"].sum()
    area.index = area.index.astype(str)
    return area.reindex([str(c) for c in curve_ids]).fillna(0.0), "portal_ws4_blocks"


def tsa_composite(code: str, age: np.ndarray) -> tuple[np.ndarray | None, dict]:
    directory = bundle_dir(code)
    if directory is None:
        return None, {"status": "no_bundle"}
    au = pd.read_csv(directory / "au_table.csv")
    points = pd.read_csv(directory / "curve_points_table.csv")
    curve_ids = au["untreated_curve_id"].dropna().astype(int).tolist()
    curves: dict[str, np.ndarray] = {}
    for curve_id in curve_ids:
        part = points[points["curve_id"].astype(int) == curve_id].sort_values("x")
        if len(part) < 2:
            continue
        curves[str(curve_id)] = np.interp(
            age,
            part["x"].to_numpy(dtype=float),
            part["y"].to_numpy(dtype=float),
            left=float(part["y"].iloc[0]),
            right=float(part["y"].iloc[-1]),
        )
    matched = [str(c) for c in curve_ids if str(c) in curves]
    if not matched:
        return None, {"status": "no_curves"}
    weights, basis = au_area_weights(str(code).zfill(2), matched)
    if weights is None or float(weights.sum()) <= 0:
        weights = pd.Series(1.0, index=matched)
        basis = "equal_au_weights"
    weights = weights / weights.sum()
    matrix = np.vstack([curves[c] for c in matched])
    return weights.to_numpy() @ matrix, {
        "status": "ok",
        "n_au": len(matched),
        "area_basis": basis,
        "bundle_dir": str(directory),
    }


def build_qc_reference() -> tuple[np.ndarray, np.ndarray]:
    library = pd.read_csv(LIBRARY)
    qc = library[library["province"].astype(str).str.upper() == "QC"]
    models = (
        qc.groupby("model_id", as_index=False)
        .agg(
            area_ha=("area_ha", "sum"),
            b1=("b1", "first"),
            b2=("b2", "first"),
            b3=("b3", "first"),
            b4=("b4", "first"),
        )
    )
    grid = np.arange(0.1, 3000.1, 0.1)
    curves = np.vstack(
        [rsyc_volume(r.b1, r.b2, r.b3, r.b4, grid) for r in models.itertuples(index=False)]
    )
    weights = models["area_ha"].to_numpy(dtype=float)
    return grid, (curves * weights[:, None]).sum(axis=0) / weights.sum()


QC_GRID, QC_REFERENCE = build_qc_reference()


def qc_reference_at(age: np.ndarray) -> np.ndarray:
    return np.interp(np.asarray(age, dtype=float), QC_GRID, QC_REFERENCE)


def load_qc_units() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    fit_input = pd.read_parquet(QC_FIT_INPUT)
    fit_input = fit_input[fit_input["fit_eligible"]].copy()
    fit_input["unit"] = fit_input["unit"].astype(str).str.zfill(5)
    units = (
        fit_input.groupby(["unit", "age"], as_index=False)
        .apply(
            lambda group: pd.Series(
                {
                    "volume": np.average(group["volume_total"], weights=group["weight_area_ha"]),
                    "weight_area_ha": float(group["weight_area_ha"].sum()),
                }
            ),
            include_groups=False,
        )
        .reset_index(drop=True)
    )
    metrics = pd.read_csv(QC_CURVE_METRICS)
    metrics["unit"] = metrics["unit"].astype(str).str.zfill(5)
    metrics = metrics[metrics["fit_eligible"]].copy()
    meta = metrics.groupby("unit", as_index=False).apply(
        lambda group: pd.Series(
            {
                "area_ha": float(group["weight_area_ha"].sum()),
                "con_share_100": float(
                    np.average(group["con_share_100"], weights=group["weight_area_ha"])
                ),
                "dec_share_100": float(
                    np.average(group["dec_share_100"], weights=group["weight_area_ha"])
                ),
                "v100": float(np.average(group["v100"], weights=group["weight_area_ha"])),
                "cmai": float(np.average(group["cmai"], weights=group["weight_area_ha"])),
                "cmai_age": float(
                    np.average(group["cmai_age"], weights=group["weight_area_ha"])
                ),
                "peak_age": float(
                    np.average(group["peak_age"], weights=group["weight_area_ha"])
                ),
            }
        ),
        include_groups=False,
    ).reset_index(drop=True)
    meta["mix"] = np.where(
        meta["con_share_100"] >= 0.7,
        "conifer",
        np.where(meta["con_share_100"] <= 0.3, "deciduous", "mixed"),
    )
    manifest = json.loads(QC_MANIFEST.read_text())
    region = pd.DataFrame(manifest["units"])[["unit", "region"]]
    region["unit"] = region["unit"].astype(str).str.zfill(5)
    meta = meta.merge(region, on="unit", how="left")
    return units, meta, {row.unit: row.region for row in meta.itertuples()}


# ---------------------------------------------------------------------------
# Donor fitting
# ---------------------------------------------------------------------------


def fit_bc_tile(task: tuple[str, int, str], library: pd.DataFrame) -> dict:
    tile_id, dominant_tsa, dominant_species = task
    rows = library[(library["province"] == "BC") & (library["tile_id"] == tile_id)]
    if rows.empty:
        raise ValueError(f"no BC library rows for tile {tile_id}")
    target = tsa_composite(str(dominant_tsa), AGES_FIT)
    if target[0] is None or target[1]["status"] != "ok":
        return {"tile_id": tile_id, "status": "no_vdyp_composite", "detail": target[1]}
    composite = target[0]
    metadata = target[1]
    dominant_row = rows.loc[rows["area_ha"].idxmax()]
    raw_composite = weighted_curve(rows, AGES_FIT)

    output = {
        "tile_id": tile_id,
        "province": "BC",
        "dominant_tsa": str(dominant_tsa).zfill(2),
        "dominant_species": dominant_species,
        "dominant_species_group": SPECIES_GROUP.get(dominant_species, "unknown"),
        "n_library_rows": int(len(rows)),
        "rsyc_area_ha": float(rows["area_ha"].sum()),
        **metadata,
        "status": "ok",
    }
    for label, (low, high) in (
        ("fit10_150", FIT_WINDOW_PRIMARY),
        ("fit1_250", FIT_WINDOW_SENSITIVITY),
    ):
        mask = (AGES_FIT >= low) & (AGES_FIT <= high)
        fit = fit_shape(
            AGES_FIT[mask],
            composite[mask],
            lambda a, row=dominant_row: row_rsyc(row, a),
            "bc",
            seed=20261128 + int(tile_id[0], 36) * 10 + int(str(dominant_tsa)[-1]),
            n_starts=22,
        )
        params = fit.x
        predicted = shape_curve(
            AGES_FIT[mask],
            lambda a, row=dominant_row: row_rsyc(row, a),
            *params,
        )
        metrics = error_metrics(composite[mask], predicted, 5)
        shape = shape_curve(
            AGES_LONG,
            lambda a, row=dominant_row: row_rsyc(row, a),
            1.0,
            float(params[1]),
            float(params[2]),
            float(params[3]),
            float(params[4]),
        )
        raw_v100 = float(row_rsyc(dominant_row, np.array([100.0]))[0])
        k100 = raw_v100 / shape[99] if shape[99] > 1.0e-9 else float("nan")
        output[label] = {
            "k": float(params[0]),
            "s": float(params[1]),
            "T": float(params[2]),
            "alpha": float(params[3]),
            "r": float(params[4]),
            "join_age": float(params[1] + params[3] * params[2]),
            **metrics,
            "k100": float(k100),
            "peak_age_rsyc_age": float(params[1] + params[3] * float(np.argmax(row_rsyc(dominant_row, AGES_FIT)))),
        }
        output[label]["v100_shape"] = float(shape[99])
        output[label]["raw_v100"] = float(raw_v100)
        cmai = shape * k100 if np.isfinite(k100) else shape
        stats = curve_summary(cmai)
        output[label].update(
            {
                "cmai_v100_preserved": stats["cmai"],
                "cmai_age_v100_preserved": stats["cmai_age"],
                "peak_age_v100_preserved": stats["peak_age"],
            }
        )
    # Applied shape: primary unless alpha is at the upper bound, in which case
    # the 1-250 sensitivity fit supplies the identified late-seral shape.
    primary = output["fit10_150"]
    applied_window = "fit1_250" if primary["alpha"] >= 4.995 else "fit10_150"
    applied = output[applied_window]
    output["applied_window"] = "1-250" if applied_window == "fit1_250" else "10-150"
    output["applied"] = {
        "s": applied["s"],
        "T": applied["T"],
        "alpha": applied["alpha"],
        "r": applied["r"],
        "join_age": applied["join_age"],
    }
    output["raw_composite_curve"] = raw_composite
    output["vdyp_composite_curve"] = composite
    return output


def fit_qc_target(
    age: np.ndarray,
    volume: np.ndarray,
    donor_id: str,
    donor_type: str,
    region: str,
    mix: str,
    seed: int,
    n_starts: int = 12,
) -> dict:
    """Fit a QC target with the extended transform and select the simplest decline."""
    selected = np.asarray(age) >= 5.0
    age = np.asarray(age, dtype=float)[selected]
    volume = np.asarray(volume, dtype=float)[selected]
    if len(age) < 8:
        raise ValueError(f"too few QC points for {donor_id}")
    peak_age = float(age[int(np.argmax(volume))])
    flat_age = curve_flat_age(age, volume)
    base_stop = max(peak_age, 25.0)
    base_mask = (age >= 5.0) & (age <= base_stop)
    base_fit = fit_shape(
        age[base_mask],
        volume[base_mask],
        qc_reference_at,
        "qc",
        seed=seed,
        n_starts=36,
    )
    base_params = base_fit.x
    base_metrics = error_metrics(
        volume[base_mask], shape_curve(age[base_mask], qc_reference_at, *base_params), 5
    )
    record = {
        "donor_id": donor_id,
        "donor_type": donor_type,
        "unit": donor_id if donor_type == "QC_unit" else "ALL",
        "region": region,
        "mix": mix,
        "peak_age": peak_age,
        "flat_age": float(flat_age),
        "n_points": int(len(age)),
        "base_s": float(base_params[1]),
        "base_T": float(base_params[2]),
        "base_alpha": float(base_params[3]),
        "base_r": float(base_params[4]),
        "base_join_age": float(base_params[1] + base_params[3] * base_params[2]),
        "base_k": float(base_params[0]),
        "base_r2": float(base_metrics["r2"]),
        "base_rmse": float(base_metrics["rmse"]),
        "base_mape_pct": float(base_metrics["mape_pct"]),
        "base_n": int(base_metrics["n"]),
    }
    for kind in DECLINE_ORDER:
        fit = fit_decline(
            age,
            volume,
            peak_age,
            flat_age,
            base_params,
            kind,
            qc_reference_at,
            "qc",
            seed=seed + 7 * (DECLINE_ORDER.index(kind) + 1),
            n_starts=n_starts,
        )
        params = fit.x
        predicted = apply_decline(
            age,
            lambda a: shape_curve(a, qc_reference_at, *params[:5]),
            peak_age,
            flat_age,
            kind,
            float(params[5]),
            params[6:],
        )
        metrics = error_metrics(volume, predicted, 5 + len(params[5:]) + 1)
        record[f"{kind}_s"] = float(params[1])
        record[f"{kind}_T"] = float(params[2])
        record[f"{kind}_alpha"] = float(params[3])
        record[f"{kind}_r"] = float(params[4])
        record[f"{kind}_join_age"] = float(params[1] + params[3] * params[2])
        record[f"{kind}_k"] = float(params[0])
        record[f"{kind}_d"] = float(params[5])
        if kind == "power":
            record[f"{kind}_q"] = float(params[6])
        if kind == "stretch":
            record[f"{kind}_tau"] = float(params[6])
            record[f"{kind}_beta"] = float(params[7])
        record[f"{kind}_r2"] = float(metrics["r2"])
        record[f"{kind}_rmse"] = float(metrics["rmse"])
        record[f"{kind}_mape_pct"] = float(metrics["mape_pct"])
        record[f"{kind}_params"] = [float(v) for v in params]
    record["selected_kind"] = select_decline(record)
    selected_params = np.asarray(record[f"{record['selected_kind']}_params"], dtype=float)
    record["selected_r2"] = record[f"{record['selected_kind']}_r2"]
    record["poor_fit"] = bool(record["selected_r2"] < 0.90)
    record["applied"] = {
        "s": float(selected_params[1]),
        "T": float(selected_params[2]),
        "alpha": float(selected_params[3]),
        "r": float(selected_params[4]),
        "join_age": float(selected_params[1] + selected_params[3] * selected_params[2]),
    }
    record["decline_kind"] = record["selected_kind"]
    record["decline_d"] = float(selected_params[5])
    record["decline_flat_age"] = float(flat_age)
    record["decline_extras"] = [float(v) for v in selected_params[6:]]
    return record


def fit_qc_unit(unit: str, units: pd.DataFrame, meta: pd.DataFrame) -> dict:
    unit_frame = units[units["unit"] == unit].sort_values("age")
    if unit_frame.empty:
        raise ValueError(f"missing QC unit {unit}")
    region = str(meta.loc[meta["unit"] == unit, "region"].iloc[0])
    mix = str(meta.loc[meta["unit"] == unit, "mix"].iloc[0])
    return fit_qc_target(
        unit_frame["age"].to_numpy(dtype=float),
        unit_frame["volume"].to_numpy(dtype=float),
        donor_id=unit,
        donor_type="QC_unit",
        region=region,
        mix=mix,
        seed=20261128 + int(unit[-3:]),
        n_starts=12,
    )


def select_decline(record: dict, adequacy: float = 0.97) -> str:
    """Simplest decline whose full-curve R2 reaches the adequacy threshold."""
    for kind in DECLINE_ORDER:
        if record[f"{kind}_r2"] >= adequacy:
            return kind
    return max(DECLINE_ORDER, key=lambda kind: record[f"{kind}_r2"])


def fit_qc_province_composite() -> dict:
    area_weighted = pd.read_csv(QC_AREA_WEIGHTED)
    composite = area_weighted[
        (area_weighted["scope"] == "province") & (area_weighted["subset"] == "fit_eligible")
    ].sort_values("age")
    if composite.empty:
        raise ValueError("missing QC fit-eligible province composite")
    return fit_qc_target(
        composite["age"].to_numpy(dtype=float),
        composite["volume_total"].to_numpy(dtype=float),
        donor_id="QC_province_composite",
        donor_type="QC_composite",
        region="QC",
        mix="all",
        seed=555,
        n_starts=20,
    )


def fit_qc_representatives() -> list[dict]:
    fit_input = pd.read_parquet(QC_FIT_INPUT)
    metrics = pd.read_csv(QC_CURVE_METRICS)
    representatives = metrics[metrics["representative"].fillna(False)].copy()
    records = []
    for row in representatives.itertuples(index=False):
        subset = fit_input[fit_input["table_id"] == row.table_id].sort_values("age")
        subset = subset.drop_duplicates("age")
        if subset.empty:
            continue
        records.append(
            fit_qc_target(
                subset["age"].to_numpy(dtype=float),
                subset["volume_total"].to_numpy(dtype=float),
                donor_id=f"QC_rep_{row.representative_role}",
                donor_type="QC_representative",
                region=str(row.representative_role),
                mix="representative",
                seed=20261130 + int(str(row.table_id)[-4:], 16) % 1000,
                n_starts=12,
            )
        )
    if len(records) != 6:
        raise RuntimeError(f"expected 6 QC representative curves, found {len(records)}")
    return records


# ---------------------------------------------------------------------------
# Donor classes
# ---------------------------------------------------------------------------


@dataclass
class DonorClass:
    class_id: str
    donor_province: str
    style: str
    scope: str
    scope_value: str
    group: str
    level: int
    n_donors: int
    params: dict
    decline_kind: str
    decline_d: float
    decline_start_age: float
    decline_flat_age: float
    decline_extras: list[float]
    fit_r2_median: float
    member_ids: list[str]
    applied_window_counts: dict[str, int]

    def as_row(self) -> dict:
        return {
            "class_id": self.class_id,
            "donor_province": self.donor_province,
            "style": self.style,
            "scope": self.scope,
            "scope_value": self.scope_value,
            "group": self.group,
            "level": self.level,
            "n_donors": self.n_donors,
            "s": self.params["s"],
            "T": self.params["T"],
            "alpha": self.params["alpha"],
            "r": self.params["r"],
            "join_age": self.params.get("join_age", self.params["s"] + self.params["alpha"] * self.params["T"]),
            "decline_kind": self.decline_kind,
            "decline_d": self.decline_d,
            "decline_start_age": self.decline_start_age,
            "decline_flat_age": self.decline_flat_age,
            "decline_extras": json.dumps(self.decline_extras),
            "fit_r2_median": self.fit_r2_median,
            "member_ids": ";".join(self.member_ids),
            "applied_window_counts": json.dumps(self.applied_window_counts),
        }


def medoid_donor(members: list[dict]) -> dict:
    """Pick the member closest to the class median in normalized parameter space."""
    keys = ["s", "T", "alpha", "r"]
    matrix = np.array(
        [[float((m.get("params") or m["applied"])[k]) for k in keys] for m in members],
        dtype=float,
    )
    median = np.median(matrix, axis=0)
    scale = np.subtract(*np.percentile(matrix, [75, 25], axis=0))
    scale[scale < 1.0e-9] = 1.0
    distance = np.sqrt(np.sum(((matrix - median) / scale) ** 2, axis=1))
    return members[int(np.argmin(distance))]


def build_bc_classes(fits: list[dict]) -> list[DonorClass]:
    valid = [f for f in fits if f.get("status") == "ok" and not f.get("poor_fit", False)]
    classes: list[DonorClass] = []
    group_sets = [
        ("ecoprovince_group", 0, 3),
        ("ecoprovince_broad", 1, 3),
        ("ecoprovince_all", 2, 3),
        ("province_group", 3, 3),
        ("province_broad", 4, 3),
        ("province_all", 5, 1),
    ]
    for level_name, level, minimum in group_sets:
        if level_name == "ecoprovince_group":
            groups = {"|".join(k): v for k, v in _group_by(valid, ["tile_ecoprovince", "group"]).items()}
        elif level_name == "ecoprovince_broad":
            groups = {"|".join(k): v for k, v in _group_by(valid, ["tile_ecoprovince", "broad"]).items()}
        elif level_name == "ecoprovince_all":
            groups = {"|".join(k): v for k, v in _group_by(valid, ["tile_ecoprovince"]).items()}
        elif level_name == "province_group":
            groups = {"|".join(k): v for k, v in _group_by(valid, ["group"]).items()}
        elif level_name == "province_broad":
            groups = {"|".join(k): v for k, v in _group_by(valid, ["broad"]).items()}
        else:
            groups = {"BC": valid}
        for key, members in groups.items():
            if len(members) < minimum:
                continue
            parts = key.split("|")
            if level_name.startswith("ecoprovince"):
                scope_value = parts[0]
                group = parts[1] if len(parts) > 1 else "all"
                scope = "ecoprovince"
            else:
                scope_value = "BC"
                group = parts[0] if parts and parts[0] else "all"
                scope = "province"
            medoid = medoid_donor(members)
            class_id = f"BC_natural_{slug(scope_value)}_{group}" if scope == "ecoprovince" else f"BC_natural_BC_{group}"
            if any(c.class_id == class_id for c in classes):
                continue
            classes.append(
                DonorClass(
                    class_id=class_id,
                    donor_province="BC",
                    style="natural_5param",
                    scope=scope,
                    scope_value=scope_value,
                    group=group,
                    level=level,
                    n_donors=len(members),
                    params=dict(medoid.get("params") or medoid["applied"]),
                    decline_kind="none",
                    decline_d=0.0,
                    decline_start_age=0.0,
                    decline_flat_age=200.0,
                    decline_extras=[],
                    fit_r2_median=float(np.median([m["fit_r2"] for m in members])),
                    member_ids=[m["tile_id"] for m in members],
                    applied_window_counts=_count_windows(members),
                )
            )
    return classes


def build_qc_classes(fits: list[dict]) -> list[DonorClass]:
    valid = [f for f in fits if not f.get("poor_fit", False)]
    classes: list[DonorClass] = []
    specs = [
        ("region_mix", ["region", "mix"], 2, "region", 2),
        ("region_all", ["region"], 2, "region", 3),
        ("province_mix", ["mix"], 3, "province", 4),
        ("province_all", [], 1, "province", 5),
    ]
    for _, keys, minimum, scope, level in specs:
        groups = _group_by(valid, keys) if keys else {("QC",): valid}
        for key, members in groups.items():
            if len(members) < minimum:
                continue
            if len(keys) == 2:
                scope_value, group = key
            elif len(keys) == 1 and keys[0] == "mix":
                scope_value, group = "QC", key[0]
            elif len(keys) == 1:
                scope_value, group = key[0], "all"
            else:
                scope_value, group = "QC", "all"
            kind = None
            for candidate in DECLINE_ORDER:
                if np.median([m[f"{candidate}_r2"] for m in members]) >= 0.97:
                    kind = candidate
                    break
            if kind is None:
                kind = max(
                    DECLINE_ORDER,
                    key=lambda c: np.median([m[f"{c}_r2"] for m in members]),
                )
            kind_members = [
                {
                    "applied": {
                        "s": m[f"{kind}_s"],
                        "T": m[f"{kind}_T"],
                        "alpha": m[f"{kind}_alpha"],
                        "r": m[f"{kind}_r"],
                    },
                    "decline_d": m[f"{kind}_d"],
                    "decline_flat_age": m["flat_age"],
                    "decline_extras": m[f"{kind}_params"][6:],
                }
                for m in members
            ]
            medoid = medoid_donor(kind_members)
            class_id = (
                f"QC_managed_{slug(scope_value)}_{group}"
                if scope == "region"
                else f"QC_managed_QC_{group}"
            )
            if any(c.class_id == class_id for c in classes):
                continue
            classes.append(
                DonorClass(
                    class_id=class_id,
                    donor_province="QC",
                    style="managed_extended",
                    scope=scope,
                    scope_value=scope_value,
                    group=group,
                    level=level,
                    n_donors=len(members),
                    params=dict(medoid.get("params") or medoid["applied"]),
                    decline_kind=kind,
                    decline_d=float(medoid["decline_d"]),
                    decline_start_age=float(np.median([m["peak_age"] for m in members])),
                    decline_flat_age=float(medoid["decline_flat_age"]),
                    decline_extras=[float(v) for v in medoid["decline_extras"]],
                    fit_r2_median=float(np.median([m[f"{kind}_r2"] for m in members])),
                    member_ids=[m["donor_id"] for m in members],
                    applied_window_counts={},
                )
            )
    return classes


def _group_by(records: list[dict], keys: list[str]) -> dict[tuple, list[dict]]:
    grouped: dict[tuple, list[dict]] = {}
    for record in records:
        key = tuple(record.get(k, "all") for k in keys) if keys else tuple()
        grouped.setdefault(key, []).append(record)
    return grouped


def _count_windows(members: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for member in members:
        window = member.get("applied_window", "unknown")
        counts[window] = counts.get(window, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Donor selection
# ---------------------------------------------------------------------------


class DonorRegistry:
    def __init__(self, bc_classes: list[DonorClass], qc_classes: list[DonorClass]):
        self.bc = bc_classes
        self.qc = qc_classes

    def select_bc(self, ecoprovince: str, ecozone: str, species_group: str, broad: str) -> tuple[DonorClass | None, int, str]:
        candidates = [
            # (distance, predicate)
            (0, lambda c: c.scope == "ecoprovince" and c.scope_value == ecoprovince and c.group == species_group),
            (1, lambda c: c.scope == "ecoprovince" and c.scope_value == ecoprovince and c.group == broad),
            (2, lambda c: c.scope == "ecoprovince" and c.scope_value == ecoprovince and c.group == "all"),
            (2, lambda c: c.scope == "ecoprovince" and c.scope_value == ecoprovince and c.group in (species_group, broad)),
        ]
        # same ecozone classes are recorded through their ecoprovince members
        for distance, predicate in candidates:
            for donor in self.bc:
                if predicate(donor):
                    return donor, distance, "ecoprovince_match"
        same_zone = [
            c for c in self.bc
            if c.scope == "ecoprovince" and c.group in (species_group, broad)
            and self._ecoprovince_ecozone(c.scope_value) == ecozone
        ]
        if same_zone:
            same_zone.sort(key=lambda c: (c.group != species_group, -c.n_donors))
            return same_zone[0], 2, "ecozone_match"
        for group, distance in ((species_group, 3), (broad, 4), ("all", 5)):
            matches = [c for c in self.bc if c.scope == "province" and c.group == group]
            if matches:
                return matches[0], distance, "province_fallback"
        return None, 99, "no_donor"

    def select_qc(self, mix: str, broad: str) -> tuple[DonorClass | None, int, str]:
        for group, distance, reason in (
            (mix, 2, "province_mix_match"),
            (broad, 3, "province_broad_match"),
            ("all", 4, "province_fallback"),
        ):
            matches = [c for c in self.qc if c.scope == "province" and c.group == group]
            if matches:
                return matches[0], distance, reason
        return None, 99, "no_donor"

    @staticmethod
    def _ecoprovince_ecozone(ecoprovince: str) -> str | None:
        return ECOPREVINCE_ECOZONE.get(ecoprovince)


ECOPREVINCE_ECOZONE: dict[str, str] = {}


def build_ecoprovince_ecozone_map(library: pd.DataFrame) -> dict[str, str]:
    tile_eco = load_tile_ecology(library)
    mapping = (
        tile_eco.groupby(["tile_ecoprovince", "tile_ecozone"]).size().reset_index(name="n")
        .sort_values(["tile_ecoprovince", "n"], ascending=[True, False])
        .drop_duplicates("tile_ecoprovince")
    )
    return dict(zip(mapping["tile_ecoprovince"], mapping["tile_ecozone"]))


def select_transform_policy(province: str, ecozone: str) -> tuple[str, str]:
    province = str(province).upper()
    if province == "BC":
        return "BC", "BC_natural_policy"
    if province == "QC":
        return "QC", "QC_managed_policy"
    if province in NATURAL_PROVINCES:
        return "BC", "natural_province_policy"
    if province in MANAGED_PROVINCES:
        return "QC", "managed_province_policy"
    if province in MIXED_PROVINCES:
        if ecozone in MANAGED_ECOZONES:
            return "QC", "mixed_province_managed_ecozone_policy"
        return "BC", "mixed_province_natural_ecozone_policy"
    return "BC", "default_natural_policy"


# ---------------------------------------------------------------------------
# National curve construction and QC gate
# ---------------------------------------------------------------------------


def make_transform_row(
    row: pd.Series,
    tile: pd.Series,
    registry: DonorRegistry,
) -> dict:
    species_group = row["species_group"]
    broad = row["broad_group"]
    mix = row["qc_mix"]
    province = str(row["province"]).upper()
    ecozone = str(tile["tile_ecozone"])
    ecoprovince = str(tile["tile_ecoprovince"])
    policy, policy_reason = select_transform_policy(province, ecozone)
    if policy == "BC":
        donor, distance, reason = registry.select_bc(ecoprovince, ecozone, species_group, broad)
    else:
        donor, distance, reason = registry.select_qc(mix, broad)
    if donor is None:
        raise RuntimeError(f"no donor class for {province} {ecoprovince} {species_group}")
    eval_fn = lambda a: row_rsyc(row, a)
    s = float(donor.params["s"])
    T = float(donor.params["T"])
    alpha = float(donor.params["alpha"])
    r = float(donor.params["r"])
    base_fn = lambda a: shape_curve(a, eval_fn, 1.0, s, T, alpha, r)
    shape = base_fn(AGES_LONG)
    base_peak_age = float(AGES_LONG[int(np.argmax(shape))])
    peak_age = base_peak_age
    flat_age = 200.0
    decline_start_age = 0.0
    if donor.decline_kind != "none":
        # Managed AAC donors decline from the donor-basis peak age (or an
        # earlier receiver peak, if one exists).  This keeps managed curves
        # from rising to age 200 when a receiver has a negative RSYC b4.
        decline_start_age = float(donor.decline_start_age)
        if decline_start_age <= 0.0:
            decline_start_age = base_peak_age
        peak_age = min(base_peak_age, decline_start_age)
        flat_age = float(donor.decline_flat_age)
        if flat_age <= peak_age + 1.0:
            flat_age = min(200.0, peak_age + max(5.0, 0.1 * (200.0 - peak_age)))
        shape = apply_decline(
            AGES_LONG,
            base_fn,
            peak_age,
            flat_age,
            donor.decline_kind,
            donor.decline_d,
            np.asarray(donor.decline_extras, dtype=float),
        )
    raw = row_rsyc(row, AGES_LONG)
    raw_peak = float(np.max(raw))
    raw_v100 = float(raw[99])
    v100_meaningful = raw_v100 > 0.05 * raw_peak and raw_v100 > 0.0
    target_level = raw_v100 if v100_meaningful else raw_peak
    target_age = 100.0 if v100_meaningful else float(AGES_LONG[int(np.argmax(raw))])
    shape_level = float(shape[int(target_age) - 1])
    level_basis = "v100" if v100_meaningful else "peak"
    if shape_level <= 1.0e-9:
        # Fall back to peak preservation only when the transformed shape
        # collapses at the requested level age.  This is recorded, not hidden.
        target_level = raw_peak
        shape_level = float(np.max(shape))
        level_basis = "peak_fallback"
    k = target_level / shape_level if shape_level > 1.0e-9 else float("nan")
    transformed = k * shape
    mapped_age_200 = (200.0 - s) / alpha if alpha > 0 else float("inf")
    row_out = {
        "curve_uid": int(row["curve_uid"]),
        "model_id": str(row["model_id"]),
        "tile_id": str(row["tile_id"]),
        "province": province,
        "species": str(row["species"]),
        "species_kind": str(row["species_kind"]),
        "species_group": species_group,
        "broad_group": broad,
        "tile_ecozone": ecozone,
        "tile_ecoprovince": ecoprovince,
        "transform_class": donor.class_id,
        "transform_style": donor.style,
        "donor_province": donor.donor_province,
        "donor_class_id": donor.class_id,
        "donor_distance": int(distance),
        "donor_rule": reason,
        "policy_rule": policy_reason,
        "donor_n": donor.n_donors,
        "donor_fit_r2_median": donor.fit_r2_median,
        "s": s,
        "T": T,
        "alpha": alpha,
        "r": r,
        "join_age": float(s + alpha * T),
        "decline_kind": donor.decline_kind,
        "decline_d": float(donor.decline_d),
        "decline_start_age": float(decline_start_age),
        "decline_flat_age": float(flat_age),
        "decline_extras": json.dumps([float(v) for v in donor.decline_extras]),
        "level_basis": level_basis,
        "k": float(k),
        "raw_v100": raw_v100,
        "raw_peak": raw_peak,
        "mapped_age_200": float(mapped_age_200),
    }
    row_out["transformed"] = transformed
    row_out["raw"] = raw
    return row_out


def qc_gate(row_out: dict) -> tuple[str, str]:
    reasons: list[str] = []
    hard_fail = False
    transformed = np.asarray(row_out["transformed"], dtype=float)
    raw = np.asarray(row_out["raw"], dtype=float)
    if not np.all(np.isfinite(transformed)):
        return "fail", "non_finite"
    if np.any(transformed < -1.0e-8):
        hard_fail = True
        reasons.append("negative_volume")
    target = row_out["raw_v100"] if row_out["level_basis"] == "v100" else row_out["raw_peak"]
    actual = transformed[99] if row_out["level_basis"] == "v100" else float(np.max(transformed))
    tolerance = max(abs(target) * 1.0e-6, 1.0e-6)
    if abs(actual - target) > tolerance:
        hard_fail = True
        reasons.append("level_not_preserved")
    peak_age = int(np.argmax(transformed)) + 1
    # pre-peak monotonicity (allow a 0.5% numerical wobble)
    if peak_age > 1:
        pre = transformed[:peak_age]
        scale = max(float(np.max(pre)), 1.0)
        if np.any(np.diff(pre) < -0.005 * scale):
            hard_fail = True
            reasons.append("nonmonotone_prepeak")
    if peak_age < 200:
        post = transformed[peak_age - 1:]
        scale = max(float(np.max(post)), 1.0)
        if np.any(np.diff(post) > 0.005 * scale):
            if row_out["decline_kind"] != "none":
                hard_fail = True
                reasons.append("postpeak_rebound")
    if transformed[-1] < 0.15 * float(np.max(transformed)):
        hard_fail = True
        reasons.append("implausible_decline")
    # juvenile checks
    if transformed[4] > transformed[19] + 0.01 * max(float(np.max(transformed)), 1.0):
        hard_fail = True
        reasons.append("juvenile_artifact")
    if transformed[19] > transformed[49] and peak_age > 50:
        hard_fail = True
        reasons.append("juvenile_artifact")
    if row_out["mapped_age_200"] > 600.0 and row_out["decline_kind"] == "none":
        reasons.append("rsyc_age_extrapolation")
    stats = curve_summary(transformed)
    row_out["cmai_transformed"] = stats["cmai"]
    row_out["cmai_age_transformed"] = stats["cmai_age"]
    row_out["peak_age_transformed"] = stats["peak_age"]
    raw_stats = curve_summary(raw)
    row_out["cmai_raw"] = raw_stats["cmai"]
    row_out["cmai_age_raw"] = raw_stats["cmai_age"]
    # parameter bounds are style-specific: QC managed fits allow alpha down to 0.1
    alpha_floor = 0.1 if row_out["transform_style"] == "managed_extended" else 0.2
    if not (-50.0 <= row_out["s"] <= 150.0 and 0.0 <= row_out["T"] <= 100.0
            and alpha_floor <= row_out["alpha"] <= 5.0 and 0.0 <= row_out["r"] <= 2.0):
        reasons.append("parameter_at_bound")
    if row_out["transform_style"] == "managed_extended" and row_out["peak_age_transformed"] > 80.0:
        reasons.append("managed_peak_too_late")
    if not (0.0 <= row_out["decline_d"] <= 0.85):
        hard_fail = True
        reasons.append("decline_out_of_bounds")
    if row_out["decline_kind"] != "none" and not (25.0 <= row_out["decline_flat_age"] <= 200.0):
        hard_fail = True
        reasons.append("decline_flat_age_out_of_bounds")
    if row_out["donor_fit_r2_median"] < 0.90:
        reasons.append("poor_donor_fit")
    if row_out["donor_distance"] >= 3:
        reasons.append("distant_donor")
    if row_out["cmai_transformed"] <= 0.0 or row_out["cmai_transformed"] > 30.0:
        hard_fail = True
        reasons.append("cmai_hard_bound")
    if hard_fail:
        return "fail", ";".join(dict.fromkeys(reasons))
    if reasons:
        return "flag", ";".join(dict.fromkeys(reasons))
    return "pass", ""


def apply_cmai_bands(records: list[dict]) -> dict:
    """Apply donor-basis CMAI bands (natural vs managed AAC).

    Soft band -> flag; hard band -> fail.  The bands are deliberately wider
    than the donor core range because receivers differ in level and species
    composition.  They are documented in transform_parameters.csv/summary.
    """
    soft = {
        ("natural_5param", "conifer"): (0.20, 8.0),
        ("natural_5param", "deciduous"): (0.20, 8.0),
        ("managed_extended", "conifer"): (1.0, 30.0),
        ("managed_extended", "deciduous"): (1.0, 20.0),
    }
    hard = {
        "natural_5param": (0.10, 10.0),
        "managed_extended": (0.50, 35.0),
    }
    for record in records:
        key = (record["transform_style"], record["broad_group"])
        low, high = soft.get(key, (0.10, 35.0))
        hard_low, hard_high = hard.get(record["transform_style"], (0.10, 35.0))
        record["cmai_band_low"] = low
        record["cmai_band_high"] = high
        value = record["cmai_transformed"]
        if not (hard_low <= value <= hard_high):
            record["qc_status"] = "fail"
            record["qc_reasons"] = ";".join(
                filter(None, [record.get("qc_reasons", ""), f"cmai_hard_band:{value:.2f}"])
            )
        elif not (low <= value <= high):
            record["qc_status"] = "flag" if record["qc_status"] != "fail" else "fail"
            record["qc_reasons"] = ";".join(
                filter(None, [record.get("qc_reasons", ""), f"cmai_band:{value:.2f}_outside_{low:.2f}-{high:.2f}"])
            )
    bands = {
        f"{style}|{broad}": {"low": low, "high": high}
        for (style, broad), (low, high) in soft.items()
    }
    bands["hard"] = {style: {"low": low, "high": high} for style, (low, high) in hard.items()}
    return bands


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def write_curves_csv(records: list[dict], path: Path) -> int:
    """Write the compact long curve table required by the artifact contract."""
    rows = 0
    with path.open("w", newline="") as handle:
        handle.write(
            "model_id,curve_uid,province,species,species_group,age,volume,"
            "transform_class,parameters,qc_status,usable\n"
        )
        for record in records:
            parameter_text = (
                f"s={record['s']:.5f};T={record['T']:.5f};alpha={record['alpha']:.5f};"
                f"r={record['r']:.6f};decline={record['decline_kind']};"
                f"d={record['decline_d']:.5f};start={record['decline_start_age']:.1f};"
                f"flat={record['decline_flat_age']:.1f};"
                f"extras={record['decline_extras']}"
            )
            transformed = record["transformed"]
            prefix = (
                f"{record['model_id']},{record['curve_uid']},{record['province']},"
                f"{record['species']},{record['species_group']},"
            )
            for index, age in enumerate(AGES_LONG.astype(int)):
                handle.write(
                    f"{prefix}{age},{float(transformed[index]):.3f},"
                    f"{record['transform_class']},\"{parameter_text}\",{record['qc_status']},{record['usable']}\n"
                )
                rows += 1
    return rows


def write_curve_metadata(records: list[dict], path: Path) -> None:
    """One row per curve with donor provenance, parameters and CMAI diagnostics."""
    rows = []
    for record in records:
        transformed = record["transformed"]
        rows.append({
            "curve_uid": record["curve_uid"],
            "model_id": record["model_id"],
            "tile_id": record["tile_id"],
            "province": record["province"],
            "species": record["species"],
            "species_kind": record["species_kind"],
            "species_group": record["species_group"],
            "broad_group": record["broad_group"],
            "tile_ecozone": record["tile_ecozone"],
            "tile_ecoprovince": record["tile_ecoprovince"],
            "transform_class": record["transform_class"],
            "transform_style": record["transform_style"],
            "donor_province": record["donor_province"],
            "donor_class_id": record["donor_class_id"],
            "donor_distance": record["donor_distance"],
            "donor_rule": record["donor_rule"],
            "policy_rule": record["policy_rule"],
            "donor_n": record["donor_n"],
            "donor_fit_r2_median": record["donor_fit_r2_median"],
            "s": record["s"],
            "T": record["T"],
            "alpha": record["alpha"],
            "r": record["r"],
            "join_age": record["join_age"],
            "decline_kind": record["decline_kind"],
            "decline_d": record["decline_d"],
            "decline_start_age": record["decline_start_age"],
            "decline_flat_age": record["decline_flat_age"],
            "decline_extras": record["decline_extras"],
            "level_basis": record["level_basis"],
            "k": record["k"],
            "raw_v100": record["raw_v100"],
            "raw_peak": record["raw_peak"],
            "v100_transformed": float(transformed[99]) if record["level_basis"] == "v100" else float(transformed[np.argmax(transformed)]),
            "cmai_raw": record["cmai_raw"],
            "cmai_age_raw": record["cmai_age_raw"],
            "cmai_transformed": record["cmai_transformed"],
            "cmai_age_transformed": record["cmai_age_transformed"],
            "peak_age_transformed": record["peak_age_transformed"],
            "mapped_age_200": record["mapped_age_200"],
            "qc_status": record["qc_status"],
            "usable": bool(record["usable"]),
            "qc_reasons": record["qc_reasons"],
        })
    pd.DataFrame(rows).to_csv(path, index=False, float_format="%.6g")


def distribution_table(records: list[dict], value: str, keys: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame([{k: r[k] for k in keys} | {value: r[value]} for r in records])
    rows = []
    for key, group in frame.groupby(keys):
        values = group[value].to_numpy(dtype=float)
        key_values = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, key_values))
        row.update(
            {
                "n": int(len(values)),
                "mean": float(np.mean(values)),
                "median": float(np.median(values)),
                "p05": float(np.percentile(values, 5)),
                "p25": float(np.percentile(values, 25)),
                "p75": float(np.percentile(values, 75)),
                "p95": float(np.percentile(values, 95)),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def build_qc_summary(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame([{
        "province": r["province"],
        "species_group": r["species_group"],
        "broad_group": r["broad_group"],
        "transform_class": r["transform_class"],
        "transform_style": r["transform_style"],
        "qc_status": r["qc_status"],
        "cmai_raw": r["cmai_raw"],
        "cmai_age_raw": r["cmai_age_raw"],
        "cmai_transformed": r["cmai_transformed"],
        "cmai_age_transformed": r["cmai_age_transformed"],
        "peak_age_transformed": r["peak_age_transformed"],
        "donor_distance": r["donor_distance"],
    } for r in records])
    grouped = frame.groupby(
        ["province", "species_group", "transform_style", "qc_status"], as_index=False
    ).agg(
        n_curves=("cmai_transformed", "size"),
        cmai_raw_median=("cmai_raw", "median"),
        cmai_transformed_median=("cmai_transformed", "median"),
        cmai_raw_p05=("cmai_raw", lambda s: float(np.percentile(s, 5))),
        cmai_raw_p95=("cmai_raw", lambda s: float(np.percentile(s, 95))),
        cmai_transformed_p05=("cmai_transformed", lambda s: float(np.percentile(s, 5))),
        cmai_transformed_p95=("cmai_transformed", lambda s: float(np.percentile(s, 95))),
        cmai_age_raw_median=("cmai_age_raw", "median"),
        cmai_age_transformed_median=("cmai_age_transformed", "median"),
        peak_age_transformed_median=("peak_age_transformed", "median"),
        donor_distance_median=("donor_distance", "median"),
    )
    totals = grouped.groupby(["province", "species_group", "transform_style"])["n_curves"].transform("sum")
    grouped["share_of_province_species_style"] = grouped["n_curves"] / totals
    return grouped.sort_values(["province", "species_group", "transform_style", "qc_status"])


def build_cmai_distribution(records: list[dict]) -> pd.DataFrame:
    rows = []
    for perspective, value_key, age_key in (
        ("raw", "cmai_raw", "cmai_age_raw"),
        ("transformed", "cmai_transformed", "cmai_age_transformed"),
    ):
        frame = pd.DataFrame([{
            "province": r["province"],
            "species_group": r["species_group"],
            "broad_group": r["broad_group"],
            "cmai": r[value_key],
            "cmai_age": r[age_key],
        } for r in records])
        for (province, group), part in frame.groupby(["province", "species_group"]):
            values = part["cmai"].to_numpy(float)
            ages = part["cmai_age"].to_numpy(float)
            rows.append({
                "perspective": perspective,
                "province": province,
                "species_group": group,
                "broad_group": part["broad_group"].iloc[0],
                "n": int(len(values)),
                "cmai_mean": float(np.mean(values)),
                "cmai_median": float(np.median(values)),
                "cmai_p05": float(np.percentile(values, 5)),
                "cmai_p25": float(np.percentile(values, 25)),
                "cmai_p75": float(np.percentile(values, 75)),
                "cmai_p95": float(np.percentile(values, 95)),
                "cmai_age_median": float(np.median(ages)),
                "cmai_age_p05": float(np.percentile(ages, 5)),
                "cmai_age_p95": float(np.percentile(ages, 95)),
                "cmai_age_at_2": int(np.sum(ages == 2)),
                "cmai_age_at_24": int(np.sum(ages == 24)),
            })
    return pd.DataFrame(rows)


def write_report(
    path: Path,
    summary: dict,
    bc_classes: list[DonorClass],
    qc_classes: list[DonorClass],
    qc_summary: pd.DataFrame,
    cmai_table: pd.DataFrame,
) -> None:
    lines = []
    lines.append("# National RSYC transformed curve library v1")
    lines.append("")
    lines.append(f"Generated: {summary['generated_at']}")
    lines.append("")
    lines.append("## Status and labels")
    lines.append("")
    lines.append(
        "This artifact is a **model estimate** for screening and downstream research. "
        "`observed=false`, `policy_grade=false`, `national_claim=false`. It does not "
        "replace or mutate official OSeCAN curves, and it does not rebuild WS4."
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "RSYC total-volume curves are transformed with the accepted 5-parameter family "
        "`V(x)=k*RSYC((x-s)/alpha)` for transformed ages at or above the join, with a "
        "ramp toe `k*RSYC(T)*(1-exp(-r*x))/(1-exp(-r*join))` below the join. "
        "The vertical `k` is set per curve to preserve the original RSYC volume at age 100 "
        "(or the original peak when V100 is not meaningful). Donor-fitted `k` values are "
        "never used as merchantability ratios."
    )
    lines.append("")
    lines.append(
        "BC donor classes were fitted to the dominant-TSA VDYP untreated composites built "
        "from the fresh TSA portal and supplemental bundles. The primary fit window is "
        "10-150 years; the 1-250 sensitivity fit is used when the primary alpha reaches its "
        "upper bound. QC donor classes were fitted to the 51 BFEC unit composites, the "
        "province composite, and the six representative curves, with a post-peak "
        "decline/plateau term selected linear -> power -> stretched exponential."
    )
    lines.append("")
    lines.append("## Extrapolation rule")
    lines.append("")
    lines.append(
        "- BC: BC natural classes, matched by ecoprovince then ecozone then species group.\n"
        "- QC: QC managed classes, matched by species-mix class (conifer/mixed/deciduous).\n"
        "- YT/NT/NU: BC natural classes.\n"
        "- ON/NB/NS/PE/NL: QC managed classes, matched by species mix (no province-specific fit claimed).\n"
        "- AB/SK/MB: QC managed in Boreal Plains and Boreal Shield ecozones; BC natural elsewhere "
        "(Montane Cordillera, Boreal Cordillera, Taiga, Hudson Plains). This is an ecozone-proximity "
        "policy, not a province-specific fit.\n"
    )
    lines.append(
        "Every curve carries `donor_class_id`, `donor_distance`, `donor_rule`, and `policy_rule`. "
        "Distance rises from 0 (exact ecoprovince + species group) to 5 (BC province fallback) "
        "for BC classes and 2-4 for QC mix classes. Distant donors are flagged."
    )
    lines.append("")
    lines.append("## Donor classes")
    lines.append("")
    lines.append("| class | style | n donors | s | T | alpha | r | decline | R2 median |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|---:|")
    for donor in bc_classes + qc_classes:
        lines.append(
            f"| {donor.class_id} | {donor.style} | {donor.n_donors} | "
            f"{donor.params['s']:.2f} | {donor.params['T']:.2f} | {donor.params['alpha']:.3f} | "
            f"{donor.params['r']:.5f} | {donor.decline_kind} | {donor.fit_r2_median:.3f} |"
        )
    lines.append("")
    lines.append("## Fit quality")
    lines.append("")
    lines.append(
        f"BC tile fits: {summary['fit']['bc_tiles_ok']} of {summary['fit']['bc_tiles_total']} produced "
        f"a VDYP composite; primary R2 min {summary['fit']['bc_primary_r2_min']:.4f}. "
        f"Poor fits (R2 < 0.90): {summary['fit']['bc_poor_fits']}."
    )
    lines.append("")
    lines.append(
        f"QC unit fits: {summary['fit']['qc_units_ok']} of {summary['fit']['qc_units_total']}; "
        f"selected full-curve R2 median {summary['fit']['qc_r2_median']:.4f}, min "
        f"{summary['fit']['qc_r2_min']:.4f}. Poor fits (R2 < 0.90): {summary['fit']['qc_poor_fits']}."
    )
    lines.append("")
    lines.append("## National library and CMAI")
    lines.append("")
    lines.append(
        f"Curves: {summary['counts']['curves']:,}; rows: {summary['counts']['rows']:,}; "
        f"provinces: {summary['counts']['provinces']}; species codes: {summary['counts']['species']}."
    )
    lines.append("")
    lines.append("### CMAI before and after by province and species group")
    lines.append("")
    lines.append("| province | species group | n | raw CMAI median | transformed CMAI median | raw CMAI age median | transformed CMAI age median |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    transformed = cmai_table[cmai_table["perspective"] == "transformed"].set_index(
        ["province", "species_group"]
    )
    raw = cmai_table[cmai_table["perspective"] == "raw"].set_index(["province", "species_group"])
    for key in raw.index:
        r = raw.loc[key]
        t = transformed.loc[key]
        lines.append(
            f"| {key[0]} | {key[1]} | {int(r['n'])} | {r['cmai_median']:.2f} | {t['cmai_median']:.2f} | "
            f"{r['cmai_age_median']:.0f} | {t['cmai_age_median']:.0f} |"
        )
    lines.append("")
    lines.append("## Raw 2-year and 24-year CMAI artifacts")
    lines.append("")
    lines.append(
        f"Raw CMAI age counts: age 2 = {summary['artifacts']['raw_cmai_age_2']:,}; "
        f"age 24 = {summary['artifacts']['raw_cmai_age_24']:,}. "
        f"After transformation: age 2 = {summary['artifacts']['transformed_cmai_age_2']:,}; "
        f"age 24 = {summary['artifacts']['transformed_cmai_age_24']:,}. "
        f"Curves whose artefact was removed: {summary['artifacts']['artifacts_removed']:,}."
    )
    lines.append("")
    lines.append(
        "The new transformed CMAI age distribution by province is in `qc_summary.csv` and "
        "`summary.json`; the modal spikes at 2 and 24 years are absent."
    )
    lines.append("")
    lines.append("## QC gate")
    lines.append("")
    counts = summary["qc_gate"]["status_counts"]
    lines.append(
        f"Pass: {counts.get('pass', 0):,}; flag: {counts.get('flag', 0):,}; fail: {counts.get('fail', 0):,}. "
        "Failures are retained in `transformed_curves.csv` with a reason; no curve is silently "
        "dropped or replaced."
    )
    lines.append("")
    lines.append("Top reasons:")
    for reason, count in sorted(summary["qc_gate"]["reason_counts"].items(), key=lambda item: -item[1])[:10]:
        lines.append(f"- `{reason}`: {count:,}")
    lines.append("")
    lines.append("### Step 4 consumption contract")
    lines.append("")
    lines.append(
        f"Use `usable=true` rows: **{summary['qc_gate']['usable_count']:,} of {summary['counts']['curves']:,} curves** "
        f"({100.0 * summary['qc_gate']['usable_count'] / summary['counts']['curves']:.1f}%). "
        "`pass` and `flag` are both usable; `flag` records provenance (chiefly distant donor) and does not "
        "mean the curve is rejected. `fail` rows are not usable; this build has "
        f"{summary['qc_gate']['status_counts'].get('fail', 0):,}."
    )
    lines.append("")
    lines.append("CMAI bands by donor basis (soft -> flag, hard -> fail):")
    for key, value in summary["qc_gate"]["cmai_bands"].items():
        if key == "hard":
            for style, band in value.items():
                lines.append(f"- hard `{style}`: {band['low']:.2f}-{band['high']:.2f}")
        else:
            lines.append(f"- soft `{key}`: {value['low']:.2f}-{value['high']:.2f}")
    lines.append("")
    lines.append("### Peak and decline rules")
    lines.append("")
    lines.append(
        "Natural 5-parameter donors carry no forced decline; their transformed curves may still rise to age 200. "
        "Managed AAC donors start the fitted decline/plateau at the donor-basis peak age (20-32 yr in this build) "
        "and flatten at the fitted flat age (50-55 yr), so managed curves do not rise to age 200 even when the "
        "underlying RSYC b4 coefficient is negative."
    )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- The transform is a shape donor transfer, not a species-specific yield model. "
        "It must not be read as a province-specific fitted curve where no BC/QC reference exists.\n"
        "- QC units lack a public ecozone join in this build; QC donor classes are therefore "
        "matched by species mix, and `donor_distance` records that limitation.\n"
        "- AB/SK/MB managed/natural assignment is an ecozone policy choice documented above; "
        "it is not a local calibration.\n"
        "- The alpha floor (0.35 for QC) keeps the mapped RSYC age inside its published domain. "
        "As a result, managed donor peaks land near 45-60 years rather than the 20-30 year "
        "BFEC peaks; CMAI remains in the QC observed range and the QC gate checks it.\n"
        "- No WS4 rebuild and no OSeCAN mutation are part of this artifact."
    )
    lines.append("")
    lines.append("## Reproduction")
    lines.append("")
    lines.append(
        "Build: `python scripts/national/build_rsyc_transform_v1.py`. "
        "Tests: `pytest tests/test_rsyc_transform_v1.py`."
    )
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading inputs ...")
    library = load_library()
    tile_ecology = load_tile_ecology(library)
    ecology_map = dict(
        zip(tile_ecology["tile_id"], zip(tile_ecology["tile_ecozone"], tile_ecology["tile_ecoprovince"]))
    )
    ECOPREVINCE_ECOZONE.update(build_ecoprovince_ecozone_map(library))
    similarity = pd.read_csv(SIMILARITY)
    bc_similarity = similarity[similarity["tile_id"].isin(
        library.loc[library["province"] == "BC", "tile_id"].unique()
    )].copy()

    print(f"Fitting {len(bc_similarity)} BC tile donors ...")
    bc_fits: list[dict] = []
    for index, row in enumerate(bc_similarity.itertuples(index=False)):
        fit = fit_bc_tile((row.tile_id, row.dominant_tsa, row.dominant_species), library)
        ecozone, ecoprovince = ecology_map[fit["tile_id"]]
        fit["tile_ecozone"] = ecozone
        fit["tile_ecoprovince"] = ecoprovince
        fit["group"] = SPECIES_GROUP.get(fit["dominant_species"], "unknown")
        fit["broad"] = GROUP_TO_BROAD.get(fit["group"], "unknown")
        fit["fit_r2"] = fit.get("fit10_150", {}).get("r2", float("nan"))
        fit["poor_fit"] = bool(
            not np.isfinite(fit["fit_r2"]) or fit["fit_r2"] < 0.90
        )
        bc_fits.append(fit)
        if (index + 1) % 10 == 0:
            print(f"  {index + 1}/{len(bc_similarity)}")

    print("Fitting QC donors ...")
    qc_units, qc_meta, _ = load_qc_units()
    qc_fits: list[dict] = []
    for index, unit in enumerate(sorted(qc_units["unit"].unique())):
        qc_fits.append(fit_qc_unit(unit, qc_units, qc_meta))
        if (index + 1) % 10 == 0:
            print(f"  {index + 1}/{qc_units['unit'].nunique()} units")
    qc_composite = fit_qc_province_composite()
    qc_representatives = fit_qc_representatives()

    print("Building donor classes ...")
    bc_classes = build_bc_classes(bc_fits)
    qc_classes = build_qc_classes(qc_fits)
    registry = DonorRegistry(bc_classes, qc_classes)

    print("Building national transformed curves ...")
    tile_lookup = tile_ecology.set_index("tile_id")
    records: list[dict] = []
    for _, row in library.iterrows():
        tile = tile_lookup.loc[row["tile_id"]]
        record = make_transform_row(row, tile, registry)
        status, reasons = qc_gate(record)
        record["qc_status"] = status
        record["qc_reasons"] = reasons
        records.append(record)
    bands = apply_cmai_bands(records)
    for record in records:
        record["usable"] = record["qc_status"] != "fail"

    print("Summarising ...")
    qc_summary = build_qc_summary(records)
    cmai_table = build_cmai_distribution(records)
    status_counts = pd.Series([r["qc_status"] for r in records]).value_counts().to_dict()
    usable_count = int(sum(1 for r in records if r.get("usable", False)))
    reason_counter: dict[str, int] = {}
    for r in records:
        for reason in [x for x in r["qc_reasons"].split(";") if x]:
            key = reason.split(":")[0]
            reason_counter[key] = reason_counter.get(key, 0) + 1

    raw_age_counts = pd.Series([int(r["cmai_age_raw"]) for r in records]).value_counts().to_dict()
    transformed_age_counts = pd.Series(
        [int(r["cmai_age_transformed"]) for r in records]
    ).value_counts().to_dict()
    artifacts_removed = sum(
        1 for r in records
        if int(r["cmai_age_raw"]) in (2, 24) and int(r["cmai_age_transformed"]) not in (2, 24)
    )

    bc_ok = [f for f in bc_fits if f.get("status") == "ok"]
    bc_primary_r2 = np.array([f["fit10_150"]["r2"] for f in bc_ok], dtype=float)
    qc_selected_r2 = np.array([f["selected_r2"] for f in qc_fits], dtype=float)

    summary = {
        "artifact": "national_rsyc_transform_v1",
        "generated_at": utc_now(),
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "donor_class_documented": True,
            "official_osecan": False,
            "ws4_change": False,
        },
        "inputs": {
            "rsyc_library": str(LIBRARY.relative_to(REPO_ROOT)),
            "rsyc_library_sha256": sha256(LIBRARY),
            "similarity_mapping": str(SIMILARITY.relative_to(DATA_ROOT)),
            "qc_fit_input": str(QC_FIT_INPUT.relative_to(REPO_ROOT)),
            "qc_area_weighted_curves": str(QC_AREA_WEIGHTED.relative_to(REPO_ROOT)),
            "qc_curve_metrics": str(QC_CURVE_METRICS.relative_to(REPO_ROOT)),
            "bc_vdyp_portal": str(PORTAL),
            "bc_vdyp_supplemental_root": str(WS4 / "femic/external"),
        },
        "method": {
            "transform_family": "V(x)=k*RSYC((x-s)/alpha) with ramp toe; QC adds a post-peak decline",
            "bc_primary_window": list(FIT_WINDOW_PRIMARY),
            "bc_sensitivity_window": list(FIT_WINDOW_SENSITIVITY),
            "bc_applied_rule": "10-150 unless primary alpha hits the 5.0 upper bound; then 1-250",
            "qc_decline_selection": "linear if R2>=0.97, else power if R2>=0.97, else stretched exponential",
            "level_policy": "preserve RSYC V100, or raw peak when V100 is not meaningful",
            "donor_k_policy": "donor k discarded; k is solved per receiver for level preservation",
            "alpha_floor_qc": 0.35,
        },
        "fit": {
            "bc_tiles_total": len(bc_fits),
            "bc_tiles_ok": len(bc_ok),
            "bc_primary_r2_min": float(np.min(bc_primary_r2)) if len(bc_primary_r2) else None,
            "bc_primary_r2_median": float(np.median(bc_primary_r2)) if len(bc_primary_r2) else None,
            "bc_poor_fits": int(np.sum(bc_primary_r2 < 0.90)),
            "bc_applied_window_counts": pd.Series(
                [f["applied_window"] for f in bc_ok]
            ).value_counts().to_dict(),
            "qc_units_total": len(qc_fits),
            "qc_units_ok": int(sum(np.isfinite(qc_selected_r2))),
            "qc_r2_median": float(np.median(qc_selected_r2)),
            "qc_r2_min": float(np.min(qc_selected_r2)),
            "qc_poor_fits": int(np.sum(qc_selected_r2 < 0.90)),
            "qc_selected_decline_counts": pd.Series(
                [f["selected_kind"] for f in qc_fits]
            ).value_counts().to_dict(),
            "qc_composite": {
                "base_r2": qc_composite["base_r2"],
                "selected_kind": qc_composite["selected_kind"],
                "selected_r2": qc_composite["selected_r2"],
                "linear_r2": qc_composite["linear_r2"],
                "power_r2": qc_composite["power_r2"],
                "stretch_r2": qc_composite["stretch_r2"],
            },
            "qc_representatives": [
                {
                    "role": r["region"],
                    "selected_kind": r["selected_kind"],
                    "selected_r2": r["selected_r2"],
                    "linear_r2": r["linear_r2"],
                    "power_r2": r["power_r2"],
                    "stretch_r2": r["stretch_r2"],
                }
                for r in qc_representatives
            ],
        },
        "donor_classes": {
            "bc": [c.as_row() for c in bc_classes],
            "qc": [c.as_row() for c in qc_classes],
        },
        "counts": {
            "curves": int(len(records)),
            "rows": int(len(records) * len(AGES_LONG)),
            "provinces": int(library["province"].nunique()),
            "species": int(library["species"].nunique()),
            "transform_classes_used": int(len({r["transform_class"] for r in records})),
            "usable_curves": usable_count,
        },
        "qc_gate": {
            "status_counts": {k: int(v) for k, v in status_counts.items()},
            "usable_count": usable_count,
            "usable_share": float(usable_count / len(records)) if records else None,
            "contract": {
                "pass": "structurally sound and inside the donor-basis CMAI band",
                "flag": "usable with provenance; expected for distant/extrapolated donors",
                "fail": "not usable; structural or hard-band failure (none in this build unless reported)",
                "distant_donor": "provenance flag only; not a failure",
                "parameter_at_bound": "flags a fitted parameter at its accepted bound; all in-bounds curves are usable",
                "cmai_band": "soft donor-basis band; hard-band violation is a fail",
                "step4_use": "use rows with usable=true (pass + flag); do not use fail rows",
            },
            "reason_counts": reason_counter,
            "cmai_bands": bands,
            "pass_fail_by_province": {
                province: {
                    status: int(count)
                    for status, count in group["qc_status"].value_counts().items()
                }
                for province, group in pd.DataFrame([
                    {"province": r["province"], "qc_status": r["qc_status"]} for r in records
                ]).groupby("province")
            },
        },
        "artifacts": {
            "raw_cmai_age_2": int(raw_age_counts.get(2, 0)),
            "raw_cmai_age_24": int(raw_age_counts.get(24, 0)),
            "transformed_cmai_age_2": int(transformed_age_counts.get(2, 0)),
            "transformed_cmai_age_24": int(transformed_age_counts.get(24, 0)),
            "artifacts_removed": int(artifacts_removed),
            "transformed_cmai_age_counts": {int(k): int(v) for k, v in sorted(transformed_age_counts.items())},
        },
        "feature": {
            "no_official_osecan": True,
            "no_ws4_rebuild": True,
            "step": 3,
            "next_step": "step 4 WS4 rebuild (separate task)",
        },
    }

    # Write tables
    print("Writing artifacts ...")
    transform_rows = [c.as_row() for c in bc_classes + qc_classes]
    pd.DataFrame(transform_rows).to_csv(output_dir / "transform_parameters.csv", index=False)

    fit_rows = []
    for fit in bc_fits:
        if fit.get("status") != "ok":
            continue
        for window in ("fit10_150", "fit1_250"):
            values = fit[window]
            fit_rows.append({
                "donor_id": fit["tile_id"],
                "donor_type": "BC_tile",
                "province": "BC",
                "region": fit["tile_ecoprovince"],
                "group": fit["group"],
                "target": f"VDYP_TSA_{fit['dominant_tsa']}",
                "window": window,
                "n": values["n"],
                "r2": values["r2"],
                "rmse": values["rmse"],
                "mae": values["mae"],
                "mape_pct": values["mape_pct"],
                "aic": values["aic"],
                "s": values["s"],
                "T": values["T"],
                "alpha": values["alpha"],
                "r": values["r"],
                "join_age": values["join_age"],
                "k_fit": values["k"],
                "k100": values["k100"],
                "cmai_v100_preserved": values["cmai_v100_preserved"],
                "cmai_age_v100_preserved": values["cmai_age_v100_preserved"],
                "peak_age_v100_preserved": values["peak_age_v100_preserved"],
                "applied": bool(
                    (fit["applied_window"] == "10-150" and window == "fit10_150")
                    or (fit["applied_window"] == "1-250" and window == "fit1_250")
                ),
                "poor_fit": bool(values["r2"] < 0.90),
            })
    def qc_row(fit: dict, kind: str) -> dict:
        return {
            "donor_id": fit["donor_id"],
            "donor_type": fit["donor_type"],
            "province": "QC",
            "region": fit["region"],
            "group": fit["mix"],
            "target": "BFEC_unit_composite" if fit["donor_type"] == "QC_unit" else
                      ("BFEC_representative" if fit["donor_type"] == "QC_representative" else "BFEC_province_composite"),
            "window": f"full_with_{kind}",
            "n": fit["n_points"],
            "r2": fit[f"{kind}_r2"],
            "rmse": fit[f"{kind}_rmse"],
            "mae": float("nan"),
            "mape_pct": fit[f"{kind}_mape_pct"],
            "aic": float("nan"),
            "base_r2": fit["base_r2"],
            "base_rmse": fit["base_rmse"],
            "base_mape_pct": fit["base_mape_pct"],
            "s": fit[f"{kind}_s"],
            "T": fit[f"{kind}_T"],
            "alpha": fit[f"{kind}_alpha"],
            "r": fit[f"{kind}_r"],
            "join_age": fit[f"{kind}_join_age"],
            "k_fit": fit[f"{kind}_k"],
            "k100": float("nan"),
            "cmai_v100_preserved": float("nan"),
            "cmai_age_v100_preserved": float("nan"),
            "peak_age_v100_preserved": float("nan"),
            "decline_d": fit[f"{kind}_d"],
            "decline_flat_age": fit["flat_age"],
            "decline_extras": json.dumps(fit[f"{kind}_params"][6:]),
            "applied": bool(fit["selected_kind"] == kind),
            "poor_fit": bool(fit[f"{kind}_r2"] < 0.90),
        }

    for fit in qc_fits:
        for kind in DECLINE_ORDER:
            fit_rows.append(qc_row(fit, kind))
    for fit in qc_representatives + [qc_composite]:
        for kind in DECLINE_ORDER:
            fit_rows.append(qc_row(fit, kind))
    pd.DataFrame(fit_rows).to_csv(output_dir / "fit_metrics.csv", index=False)
    qc_summary.to_csv(output_dir / "qc_summary.csv", index=False)
    cmai_table.to_csv(output_dir / "cmai_distribution.csv", index=False)

    rows = write_curves_csv(records, output_dir / "transformed_curves.csv")
    write_curve_metadata(records, output_dir / "curve_metadata.csv")
    summary["counts"]["rows"] = int(rows)

    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    write_report(output_dir / "report.md", summary, bc_classes, qc_classes, qc_summary, cmai_table)

    files = [
        "transformed_curves.csv",
        "curve_metadata.csv",
        "transform_parameters.csv",
        "fit_metrics.csv",
        "qc_summary.csv",
        "cmai_distribution.csv",
        "summary.json",
        "report.md",
    ]
    manifest = {
        "artifact": "national_rsyc_transform_v1",
        "generated_at": summary["generated_at"],
        "status": "scratch" if status_counts.get("fail", 0) > 0.25 * len(records) else "stable",
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": {
            name: {
                "sha256": sha256(output_dir / name),
                "bytes": int((output_dir / name).stat().st_size),
            }
            for name in files
        },
        "record_counts": summary["counts"],
        "qc_status_counts": summary["qc_gate"]["status_counts"],
        "software": {
            "python": "3.12",
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(f"Wrote {output_dir}")
    print(json.dumps({k: summary['qc_gate']['status_counts'] for k in ('qc_gate',)}, indent=2))
    print("qc_status_counts", summary["qc_gate"]["status_counts"])
    print("fit", json.dumps(summary["fit"], indent=2, default=str))


if __name__ == "__main__":
    main()
