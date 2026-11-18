"""Benchmark RSYC national volume curves against BC, QC, and MGM datasets.

The module is intentionally split into small pure helpers (curve evaluation,
metrics, species parsing/mapping, matching) and a command-line orchestration
layer.  All data are parsed at the boundary; downstream functions receive
trusted numeric or categorical values and fail loudly on invalid states.

Run from the repository root:

    .venv/bin/python -m workflows.national.rsyc_benchmark.benchmark \
        --out-dir data/derived/national/rsyc_benchmark

The MGM CSVs are expected under ``$CLEWS_EXTERNAL_DATA_ROOT/mgm`` or the
sibling ``external/mgm`` directory; pass ``--mgm-dir`` to override.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants and domain tables
# ---------------------------------------------------------------------------

RSYC_MAX_OPERATIONAL_AGE = 150
RSYC_MIN_AGE = 1

BC_AGE_BINS = ((40, 60), (80, 100), (120, 140))
QC_AGES = (50, 100, 150)
MGM_AGES = (50, 100, 150)

BROADLEAF_SPECIES = frozenset(
    {
        "ACER.MAC",
        "ACER.RUB",
        "ACER.SAH",
        "ALNU.RUB",
        "BETU.ALL",
        "BETU.PAP",
        "FRAX.NIG",
        "POPU.BAL",
        "POPU.GRA",
        "POPU.TRE",
        "QUER.RUB",
        "BROADLEAF",
    }
)

# BC JSP code -> RSYC scientific code.  Only biologically defensible direct
# matches or explicit proxies are included.  Codes absent here stay unmapped so
# they are counted rather than silently imputed.
BC_TO_RSYC = {
    "AC": "POPU.BAL",  # black cottonwood -> balsam poplar complex
    "AT": "POPU.TRE",
    "BA": "ABIE.AMA",  # amabilis fir
    "BG": "ABIE.AMA",  # grand fir proxy (no ABIE.GRA in RSYC)
    "BL": "ABIE.LAS",
    "CW": "THUJ.PLI",
    "DR": "ALNU.RUB",
    "FD": "PSEU.MEN",
    "HM": "TSUG.MER",
    "HW": "TSUG.HET",
    "LW": "LARI.OCC",
    "MB": "ACER.MAC",
    "PJ": "PINU.BAN",
    "PL": "PINU.CON",
    "PY": "PINU.PON",
    "SB": "PICE.MAR",
    "SE": "PICE.ENG",
    "SS": "PICE.SIT",
    "SW": "PICE.GLA",
    "YC": "THUJ.OCC",
}

# MGM yield-group species code -> RSYC scientific code (documented in report).
MGM_TO_RSYC = {
    "Sb": "PICE.MAR",
    "Sw": "PICE.GLA",
    "Pj": "PINU.BAN",
    "Aw": "POPU.TRE",
}

# MGM is a live-tree volume model.  The exact expression was verified against
# the pinned upstream loader at UBC-FRESH/spades_ws3 commit 1847872,
# python/spadesws3.py, bootstrap_yields.f.  Age is in years, age origin is 0,
# and total volume is the sum of the conifer (cvol) and deciduous (dvol)
# components.  The upstream curve is sampled at 0..350 years in 10-year steps.
MGM_SOURCE_COMMIT = "1847872"
MGM_SOURCE_REPOSITORY = "https://github.com/UBC-FRESH/spades_ws3"
MGM_SOURCE_FILE = "python/spadesws3.py"
MGM_SOURCE_FUNCTION = "bootstrap_yields.f"
MGM_NATIVE_MAX_AGE = 350
MGM_PERIOD_LENGTH = 10
MGM_EQUATION = "V(age) = c_a*age**c_b*exp(-c_c*age) + d_a*age**d_b*exp(-d_c*age)"
MGM_EQUATION_NOTE = (
    "Verified against UBC-FRESH/spades_ws3 commit "
    f"{MGM_SOURCE_COMMIT} {MGM_SOURCE_FILE} {MGM_SOURCE_FUNCTION}: "
    "V = a*pow(x,b)*exp(-c*x), x in years from age 0, c and d components "
    "summed for total volume. Absolute levels are conditional on the pinned "
    "coefficients; MGM still has no spatial/FMU join key."
)

# Quebec BFEC administrative region -> RSYC ecoprovince (coarse, no geometry).
QC_REGION_TO_ECOPROVINCE = {
    "R03": "Atlantic Maritime/Appalachian-Acadian Highlands",
    "R04": "Boreal Shield East/Southern Boreal Shield",
    "R12": "Atlantic Maritime/Appalachian-Acadian Highlands",
}

QC_UNIT_TO_REGION = {
    3451: "R12",
    2651: "R04",
    3152: "R03",
    3551: "R12",
    3151: "R03",
}

# Explicit BEC zone vocabulary.  BEC labels are zone + subzone/variant, e.g.
# CWHvm1, CHxw, MHmm1, SBPSmc.  Sorting by length avoids the old three-letter
# slice that turned CH/MH into CHx/MHm and lost the stratum.
BEC_ZONES = frozenset(
    {
        "CWH",
        "CH",
        "MH",
        "CDF",
        "BWBS",
        "SWB",
        "SBS",
        "SBPS",
        "ESSF",
        "ICH",
        "IDF",
        "MS",
        "PP",
        "BG",
    }
)

# Fallback BEC-zone -> RSYC ecoprovince when a TSA is absent from the VRI
# area-weight table.  Kept deliberately coarse; counted in the manifest.  CWH
# is assigned to the southern coastal mountains as a conservative default and
# is always grouped under the bec_zone_fallback basis so it cannot be mistaken
# for a TSA-dominant assignment.
BEC_ZONE_TO_ECOPROVINCE = {
    "CWH": "Pacific Maritime/Southern Coastal Mountains",
    "CH": "Pacific Maritime/Northern Coastal Mountains",
    "MH": "Pacific Maritime/Northern Coastal Mountains",
    "CDF": "Pacific Maritime/Georgia Depression",
    "BWBS": "Boreal Plains/Central Boreal Plains",
    "SWB": "Boreal Plains/Central Boreal Plains",
    "SBS": "Central Montane Cordillera",
    "SBPS": "Central Montane Cordillera",
    "ESSF": "Southern Montane Cordillera",
    "ICH": "Southern Montane Cordillera",
    "IDF": "Southern Montane Cordillera",
    "MS": "Southern Montane Cordillera",
    "PP": "Southern Montane Cordillera",
    "BG": "Southern Montane Cordillera",
}

# Quebec BFEC component/total validation.  The source tables are expected to
# satisfy conifer + deciduous == total; a mismatch invalidates the derived mix.
QC_MASS_BALANCE_RTOL = 1e-6
QC_MASS_BALANCE_ATOL = 1e-6

MGM_YGRP_RE = re.compile(
    r"^(?P<prefix>[A-Za-z]+)-(?P<species>[A-Za-z]+)_(?P<height>\d+)_"
    r"(?P<density>[A-Z])_(?P<age>\d+)_(?P<crown>\d+)$"
)


# ---------------------------------------------------------------------------
# Parsed curve representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RsycCurve:
    """One published RSYC volume curve."""

    model_id: str
    strata_id: str
    species: str
    model_group: str
    b1: float
    b2: float
    b3: float
    b4: float

    def volume(self, ages: np.ndarray | float) -> np.ndarray:
        return evaluate_rsyc_curve(
            np.asarray(ages, dtype=float), self.b1, self.b2, self.b3, self.b4
        )


def evaluate_rsyc_curve(
    ages: np.ndarray, b1: float, b2: float, b3: float, b4: float
) -> np.ndarray:
    """Evaluate the published RSYC volume equation.

    ``y = b1 * exp(-b4 * age) * (1 - exp(-b2 * age)) ** b3``

    Ages outside the operational 1-150 year window are rejected rather than
    silently extrapolated; callers must filter observations first.
    """

    ages = np.asarray(ages, dtype=float)
    if ages.ndim == 0:
        ages = ages.reshape(1)
    invalid = (
        ~np.isfinite(ages) | (ages < RSYC_MIN_AGE) | (ages > RSYC_MAX_OPERATIONAL_AGE)
    )
    if invalid.any():
        bad = ages[invalid][:3]
        raise ValueError(
            f"RSYC age outside operational {RSYC_MIN_AGE}-"
            f"{RSYC_MAX_OPERATIONAL_AGE} year window: {bad.tolist()}"
        )
    growth = 1.0 - np.exp(-b2 * ages)
    return b1 * np.exp(-b4 * ages) * np.power(growth, b3)


def load_rsyc_models(models_path: Path) -> pd.DataFrame:
    """Read and validate the RSYC national ecoprovince volume catalogue."""

    if not models_path.exists():
        raise FileNotFoundError(f"RSYC model catalogue not found: {models_path}")
    opener = zipfile.ZipFile if models_path.suffix == ".zip" else None
    if opener is None:
        raw = pd.read_csv(models_path)
    else:
        with opener(models_path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(members) != 1:
                raise ValueError(
                    f"Expected exactly one CSV in {models_path}, found {members}"
                )
            with archive.open(members[0]) as handle:
                raw = pd.read_csv(handle)

    required = {
        "model_id",
        "response",
        "strata_level",
        "strata_id",
        "species",
        "model_group",
        "scale",
        "b1",
        "b2",
        "b3",
        "b4",
    }
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"RSYC catalogue missing columns: {sorted(missing)}")

    models = raw.loc[
        (raw["response"] == "volume")
        & (raw["scale"] == "national")
        & (raw["strata_level"].isin(["ecoprovince", "ecozone"]))
    ].copy()
    if models.empty:
        raise ValueError(
            "No national ecoprovince volume curves found in RSYC catalogue"
        )
    if models["model_id"].duplicated().any():
        duplicates = models.loc[models["model_id"].duplicated(), "model_id"].head(5)
        raise ValueError(f"Duplicate RSYC model ids: {duplicates.tolist()}")
    numeric = models[["b1", "b2", "b3", "b4"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("Non-numeric RSYC curve parameters found")
    models[["b1", "b2", "b3", "b4"]] = numeric.astype(float)
    return models.reset_index(drop=True)


class RsycLookup:
    """Resolve RSYC curves with the same keys used by the national fit.

    Resolution order is explicit and recorded:
    national-fit cell model -> species model -> coniferous/broadleaf group
    model -> generic model.  A missing curve raises ``KeyError``; no parameter
    averaging or cross-ecoprovince borrowing is invented here.
    """

    def __init__(
        self,
        models: pd.DataFrame,
        cell_models: Mapping[tuple[str, str], str],
    ) -> None:
        self._by_id = {
            row.model_id: RsycCurve(
                model_id=row.model_id,
                strata_id=row.strata_id,
                species=row.species,
                model_group=row.model_group,
                b1=float(row.b1),
                b2=float(row.b2),
                b3=float(row.b3),
                b4=float(row.b4),
            )
            for row in models.itertuples(index=False)
        }
        self._cell_models = dict(cell_models)
        self._by_species = {
            (row.strata_id, row.species): self._by_id[row.model_id]
            for row in models.itertuples(index=False)
            if row.model_group == "species"
        }
        self._by_group = {
            (row.strata_id, row.model_group): self._by_id[row.model_id]
            for row in models.itertuples(index=False)
            if row.model_group in {"coniferous", "broadleaf", "generic"}
        }

    def curve_for(self, strata_id: str, species: str) -> tuple[RsycCurve, str]:
        cell_model_id = self._cell_models.get((strata_id, species))
        if cell_model_id is not None and cell_model_id in self._by_id:
            return self._by_id[cell_model_id], "national_fit_cell"

        species_curve = self._by_species.get((strata_id, species))
        if species_curve is not None:
            return species_curve, "species"

        group = "broadleaf" if species in BROADLEAF_SPECIES else "coniferous"
        group_curve = self._by_group.get((strata_id, group))
        if group_curve is not None:
            return group_curve, f"{group}_group"

        generic_curve = self._by_group.get((strata_id, "generic"))
        if generic_curve is not None:
            return generic_curve, "generic_group"

        raise KeyError(
            f"No RSYC volume curve for strata_id={strata_id!r}, species={species!r}"
        )

    def predict(self, strata_id: str, species: str, age: float) -> tuple[float, str]:
        curve, level = self.curve_for(strata_id, species)
        return float(curve.volume(np.array([age]))[0]), level


def load_cell_models(path: Path) -> dict[tuple[str, str], str]:
    """Load the ecoprovince cell -> RSYC model id mapping from the national fit."""

    cells = pd.read_csv(
        path,
        usecols=["level", "stratum_id", "species", "rsyc_volume_model_id"],
    )
    ecoprovince = cells.loc[cells["level"] == "ecoprovince"]
    if ecoprovince.empty:
        raise ValueError(f"No ecoprovince rows in national fit table: {path}")
    mapping = {
        (str(row.stratum_id), str(row.species)): str(row.rsyc_volume_model_id)
        for row in ecoprovince.itertuples(index=False)
        if pd.notna(row.rsyc_volume_model_id)
    }
    if not mapping:
        raise ValueError("National fit table has no usable rsyc_volume_model_id values")
    return mapping


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def regression_metrics(
    observed: Sequence[float] | np.ndarray,
    predicted: Sequence[float] | np.ndarray,
) -> dict[str, float | int]:
    """Return n, bias, MAE, RMSE, relative errors, correlation, and hit rates.

    ``bias`` and RMSE are in m3/ha; relative values divide by mean observed
    volume.  Hit rates use per-observation prediction ratios, which is the
    operational question ("is the curve within +/-20% of this stand?").
    """

    obs = np.asarray(observed, dtype=float)
    pred = np.asarray(predicted, dtype=float)
    if obs.shape != pred.shape:
        raise ValueError(
            f"Shape mismatch: observed {obs.shape} vs predicted {pred.shape}"
        )
    valid = np.isfinite(obs) & np.isfinite(pred)
    obs = obs[valid]
    pred = pred[valid]
    n = int(obs.size)
    if n == 0:
        return {
            "n": 0,
            "n_positive_observed": 0,
            "n_zero_observed": 0,
            "within_20pct_denominator": 0,
            "within_30pct_denominator": 0,
            "mean_observed": math.nan,
            "mean_predicted": math.nan,
            "bias": math.nan,
            "mae": math.nan,
            "rmse": math.nan,
            "relative_bias": math.nan,
            "relative_rmse": math.nan,
            "correlation": math.nan,
            "within_20pct": math.nan,
            "within_30pct": math.nan,
        }

    residual = pred - obs
    mean_obs = float(obs.mean())
    bias = float(residual.mean())
    mae = float(np.abs(residual).mean())
    rmse = float(np.sqrt(np.mean(np.square(residual))))
    mean_pred = float(pred.mean())
    rel_bias = bias / mean_obs if mean_obs != 0 else math.nan
    rel_rmse = rmse / mean_obs if mean_obs != 0 else math.nan

    if n >= 3 and obs.std(ddof=0) > 0 and np.ptp(pred) > 0:
        correlation = float(np.corrcoef(obs, pred)[0, 1])
    else:
        correlation = math.nan

    positive = obs > 0
    ratio = np.full(n, np.nan)
    ratio[positive] = pred[positive] / obs[positive]
    within_20 = (
        float(np.mean(np.abs(ratio[positive] - 1.0) <= 0.20))
        if positive.any()
        else math.nan
    )
    within_30 = (
        float(np.mean(np.abs(ratio[positive] - 1.0) <= 0.30))
        if positive.any()
        else math.nan
    )

    return {
        "n": n,
        "n_positive_observed": int(positive.sum()),
        "n_zero_observed": int((obs == 0).sum()),
        "within_20pct_denominator": int(positive.sum()),
        "within_30pct_denominator": int(positive.sum()),
        "mean_observed": mean_obs,
        "mean_predicted": mean_pred,
        "bias": bias,
        "mae": mae,
        "rmse": rmse,
        "relative_bias": rel_bias,
        "relative_rmse": rel_rmse,
        "correlation": correlation,
        "within_20pct": within_20,
        "within_30pct": within_30,
    }


def summarise_pairs(
    frame: pd.DataFrame,
    group_columns: Sequence[str],
    observed_column: str = "observed_volume",
    predicted_column: str = "predicted_volume",
) -> pd.DataFrame:
    """Group a paired observation frame and apply ``regression_metrics``."""

    if frame.empty:
        return pd.DataFrame(
            columns=[*group_columns, *regression_metrics([], []).keys()]
        )
    rows: list[dict[str, object]] = []
    if not group_columns:
        row = regression_metrics(
            frame[observed_column].to_numpy(), frame[predicted_column].to_numpy()
        )
        return pd.DataFrame([row])
    grouper = group_columns[0] if len(group_columns) == 1 else list(group_columns)
    for key, group in frame.groupby(grouper, dropna=False, sort=True):
        key_values = (key,) if len(group_columns) == 1 else key
        row = dict(zip(group_columns, key_values, strict=True))
        row.update(
            regression_metrics(
                group[observed_column].to_numpy(), group[predicted_column].to_numpy()
            )
        )
        rows.append(row)
    return pd.DataFrame(rows)


def area_weighted_mean(
    weights: Sequence[float] | np.ndarray, values: Sequence[float] | np.ndarray
) -> float:
    """Weighted mean that rejects empty or non-positive weight vectors."""

    weights_array = np.asarray(weights, dtype=float)
    values_array = np.asarray(values, dtype=float)
    if weights_array.shape != values_array.shape:
        raise ValueError("weights and values must have matching shapes")
    valid = np.isfinite(weights_array) & np.isfinite(values_array) & (weights_array > 0)
    if not valid.any():
        raise ValueError("Cannot form an area-weighted mean without positive weights")
    return float(np.average(values_array[valid], weights=weights_array[valid]))


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def age_bin_label(
    age: float, bins: Iterable[tuple[int, int]] = BC_AGE_BINS
) -> str | None:
    """Return the inclusive age-bin label or ``None`` if outside all bins."""

    for low, high in bins:
        if low <= age <= high:
            return f"{low}-{high}"
    return None


def canonical_species_code(raw_code: object, mapping: Mapping[str, str]) -> str | None:
    """Normalise a BC/MGM species code to an RSYC scientific code.

    Accepts three forms:
    * canonical RSYC codes (optionally suffixed ``.NA`` / ``.BC``),
    * BC JSP codes found in ``mapping``,
    * MGM yield-group codes found in ``mapping``.
    """

    if raw_code is None or (isinstance(raw_code, float) and math.isnan(raw_code)):
        return None
    code = str(raw_code).strip()
    if not code:
        return None
    if "." in code:
        parts = code.split(".")
        if len(parts) >= 2 and parts[0] in {
            "ABIE",
            "ACER",
            "ALNU",
            "BETU",
            "FRAX",
            "LARI",
            "PICE",
            "PINU",
            "POPU",
            "PSEU",
            "QUER",
            "THUJ",
            "TSUG",
        }:
            if code.endswith((".NA", ".BC")):
                return code.rsplit(".", 1)[0]
            return code
    return mapping.get(code)


def parse_bec_zone(bec: object) -> tuple[str | None, str | None]:
    """Return ``(zone, subzone)`` from a BEC label.

    Examples: ``CWHvm1 -> ('CWH', 'vm')``, ``CHxw -> ('CH', 'xw')``,
    ``MHmm1 -> ('MH', 'mm')``, ``SBPSmc -> ('SBPS', 'mc')``.  Unknown labels
    such as ``CMAunp`` return ``(None, None)`` rather than a bogus slice.
    """

    if bec is None or (isinstance(bec, float) and math.isnan(bec)):
        return None, None
    text = str(bec).strip()
    if not text:
        return None, None
    for zone in sorted(BEC_ZONES, key=len, reverse=True):
        if text.startswith(zone):
            remainder = text[len(zone) :]
            match = re.match(r"[a-z]+", remainder)
            return zone, match.group(0) if match else None
    return None, None


@dataclass(frozen=True)
class BcGeography:
    """Resolved BC visit geography, including the assignment basis."""

    ecoprovince: str
    ecoprovince_stratum: str
    mapping_source: str
    bec_zone: str | None
    bec_subzone: str | None
    detail: str | None = None


def resolve_bc_geography(
    tsa_name: str,
    bec: object,
    tsa_ecoprovince: Mapping[str, str],
    stratum_by_ecoprovince: Mapping[str, str],
    valid_strata: set[str],
) -> BcGeography | None:
    """Resolve a BC visit to a validated RSYC stratum or return ``None``.

    The TSA-dominant VRI assignment is preferred.  If the TSA is missing, the
    BEC zone fallback is used.  The fallback result carries a real ecoprovince
    name and is validated against the area-weight stratum keys, so fallback
    visits no longer collapse into a null/blank bucket.
    """

    bec_zone, bec_subzone = parse_bec_zone(bec)
    tsa_ecoprovince_name = tsa_ecoprovince.get(str(tsa_name))
    if tsa_ecoprovince_name is not None:
        stratum = stratum_by_ecoprovince.get(str(tsa_ecoprovince_name))
        if stratum is not None and stratum in valid_strata:
            return BcGeography(
                ecoprovince=str(tsa_ecoprovince_name),
                ecoprovince_stratum=stratum,
                mapping_source="tsa_dominant_vri",
                bec_zone=bec_zone,
                bec_subzone=bec_subzone,
            )

    fallback_stratum = BEC_ZONE_TO_ECOPROVINCE.get(bec_zone) if bec_zone else None
    if fallback_stratum is not None and fallback_stratum in valid_strata:
        return BcGeography(
            ecoprovince=fallback_stratum.split("/", 1)[-1],
            ecoprovince_stratum=fallback_stratum,
            mapping_source="bec_zone_fallback",
            bec_zone=bec_zone,
            bec_subzone=bec_subzone,
            detail=(
                "tsa_not_in_vri_area_table"
                if tsa_ecoprovince_name is None
                else "tsa_ecoprovince_not_in_stratum_map"
            ),
        )
    return None


def parse_mgm_ygrp(ygrp: object) -> dict[str, object] | None:
    """Parse an MGM yield-group key into its components."""

    if ygrp is None or (isinstance(ygrp, float) and math.isnan(ygrp)):
        return None
    match = MGM_YGRP_RE.match(str(ygrp).strip())
    if match is None:
        return None
    groups = match.groupdict()
    return {
        "prefix": groups["prefix"],
        "species_code": groups["species"],
        "height": int(groups["height"]),
        "density": groups["density"],
        "age": int(groups["age"]),
        "crown_closure": int(groups["crown"]),
    }


def leading_species(
    ba_by_species: Mapping[str, float], mapping: Mapping[str, str]
) -> tuple[str | None, float]:
    """Return (leading mapped RSYC species, mapped BA share).

    Unmapped species are excluded from the leading-species decision but remain
    in the denominator, so a low mapped share is visible to the caller.
    """

    total = float(sum(value for value in ba_by_species.values() if value > 0))
    if total <= 0:
        return None, 0.0
    mapped: dict[str, float] = {}
    for species, ba in ba_by_species.items():
        if ba <= 0:
            continue
        rsyc = canonical_species_code(species, mapping)
        if rsyc is not None:
            mapped[rsyc] = mapped.get(rsyc, 0.0) + float(ba)
    if not mapped:
        return None, 0.0
    leader = max(mapped.items(), key=lambda item: item[1])[0]
    return leader, float(sum(mapped.values()) / total)


# ---------------------------------------------------------------------------
# Input adapters
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_external_root(repo_root: Path) -> Path:
    sibling = repo_root.parent / "external" / "mgm"
    if sibling.exists():
        return sibling
    env_root = Path.cwd().parent / "external" / "mgm"
    if env_root.exists():
        return env_root
    return sibling


def ecoprovince_stratum_map(area_weights_path: Path) -> dict[str, str]:
    """Map bare ecoprovince names to RSYC ``Ecozone/Ecoprovince`` keys."""

    weights = pd.read_csv(
        area_weights_path, usecols=["ecozone", "ecoprovince"]
    ).dropna()
    pairs = weights.drop_duplicates()
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for row in pairs.itertuples(index=False):
        key = f"{row.ecozone}/{row.ecoprovince}"
        existing = mapping.get(str(row.ecoprovince))
        if existing is not None and existing != key:
            ambiguous.add(str(row.ecoprovince))
        mapping[str(row.ecoprovince)] = key
    for name in ambiguous:
        mapping.pop(name, None)
    if not mapping:
        raise ValueError(f"No ecoprovince keys derived from {area_weights_path}")
    return mapping


def tsa_to_ecoprovince(area_weights_path: Path) -> dict[str, str]:
    """Choose the dominant RSYC ecoprovince for each BC TSA by VRI area."""

    weights = pd.read_csv(
        area_weights_path, usecols=["tsa_name", "ecoprovince", "area_ha"]
    )
    totals = (
        weights.groupby(["tsa_name", "ecoprovince"], as_index=False)["area_ha"]
        .sum()
        .sort_values(["tsa_name", "area_ha"], ascending=[True, False])
        .drop_duplicates("tsa_name")
    )
    return {
        str(row.tsa_name): str(row.ecoprovince)
        for row in totals.itertuples(index=False)
    }


def load_bc_species_composition(trees_path: Path, visit_ids: set[str]) -> pd.DataFrame:
    """Aggregate live-tree basal area by visit and species for BC FAIB visits."""

    trees = pd.read_csv(
        trees_path,
        usecols=["visit_id", "species_code", "dbh_cm", "status", "expansion_factor"],
        low_memory=False,
    )
    trees = trees.loc[trees["visit_id"].isin(visit_ids)].copy()
    trees = trees.loc[
        (trees["status"].astype(str).str.lower() == "live")
        & (pd.to_numeric(trees["dbh_cm"], errors="coerce") > 0)
        & (pd.to_numeric(trees["expansion_factor"], errors="coerce") > 0)
    ]
    if trees.empty:
        raise ValueError("No live BC trees matched the requested visits")
    trees["dbh_cm"] = pd.to_numeric(trees["dbh_cm"], errors="coerce")
    trees["expansion_factor"] = pd.to_numeric(
        trees["expansion_factor"], errors="coerce"
    )
    trees["basal_area"] = (
        math.pi / 4.0 * np.square(trees["dbh_cm"] / 100.0) * trees["expansion_factor"]
    )
    composition = (
        trees.groupby(["visit_id", "species_code"], as_index=False)["basal_area"]
        .sum()
        .rename(columns={"basal_area": "ba_m2ha"})
    )
    return composition


# ---------------------------------------------------------------------------
# Benchmark builders
# ---------------------------------------------------------------------------


def build_bc_benchmark(
    visits_path: Path,
    trees_path: Path,
    bc_area_weights_path: Path,
    national_area_weights_path: Path,
    lookup: RsycLookup,
    *,
    min_age: int = 20,
    max_age: int = RSYC_MAX_OPERATIONAL_AGE,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Compare BC FAIB visit volume with RSYC at the same age/species/ecoprovince."""

    visits = pd.read_csv(
        visits_path,
        usecols=[
            "BEC",
            "TSA_DESC",
            "SITE_IDENTIFIER",
            "CLSTR_ID",
            "year",
            "age",
            "volume",
            "top_height_m",
            "source",
        ],
    )
    visits = visits.loc[
        visits["volume"].notna()
        & (visits["volume"] > 0)
        & visits["age"].notna()
        & visits["age"].between(min_age, max_age)
    ].copy()
    if visits.empty:
        raise ValueError("No BC visits remain after volume/age filtering")

    composition = load_bc_species_composition(trees_path, set(visits["CLSTR_ID"]))
    ba_by_visit = {
        str(visit_id): {
            str(row.species_code): float(row.ba_m2ha)
            for row in group.itertuples(index=False)
        }
        for visit_id, group in composition.groupby("visit_id", sort=False)
    }

    stratum_by_ecoprovince = ecoprovince_stratum_map(national_area_weights_path)
    tsa_ecoprovince = tsa_to_ecoprovince(bc_area_weights_path)

    valid_strata = set(stratum_by_ecoprovince.values()) | set(
        BEC_ZONE_TO_ECOPROVINCE.values()
    )

    rows: list[dict[str, object]] = []
    skip_counts = {
        "no_composition": 0,
        "no_code_mapped_leader": 0,
        "no_geography": 0,
        "no_curve": 0,
    }
    fallback_zone_counts: dict[str, int] = {}
    fallback_reason_counts: dict[str, int] = {}
    for visit in visits.itertuples(index=False):
        ba_map = ba_by_visit.get(str(visit.CLSTR_ID))
        if not ba_map:
            skip_counts["no_composition"] += 1
            continue
        leader, mapped_share = leading_species(ba_map, BC_TO_RSYC)
        if leader is None:
            skip_counts["no_code_mapped_leader"] += 1
            continue
        tsa_name = str(visit.TSA_DESC).replace(" TSA", "").strip()
        geography = resolve_bc_geography(
            tsa_name,
            visit.BEC,
            tsa_ecoprovince,
            stratum_by_ecoprovince,
            valid_strata,
        )
        if geography is None:
            skip_counts["no_geography"] += 1
            continue
        if geography.mapping_source == "bec_zone_fallback":
            zone = geography.bec_zone or "UNKNOWN"
            fallback_zone_counts[zone] = fallback_zone_counts.get(zone, 0) + 1
            reason = geography.detail or "unspecified"
            fallback_reason_counts[reason] = fallback_reason_counts.get(reason, 0) + 1

        age = float(visit.age)
        try:
            predicted_leader, match_level = lookup.predict(
                geography.ecoprovince_stratum, leader, age
            )
            composite_prediction = _composite_prediction(
                lookup, geography.ecoprovince_stratum, ba_map, age
            )
        except KeyError:
            skip_counts["no_curve"] += 1
            continue

        bin_label = age_bin_label(age)
        rows.append(
            {
                "dataset": "BC_FAIB",
                "visit_id": str(visit.CLSTR_ID),
                "tsa": tsa_name,
                "bec_zone": geography.bec_zone,
                "bec_subzone": geography.bec_subzone,
                "ecoprovince": geography.ecoprovince,
                "ecoprovince_stratum": geography.ecoprovince_stratum,
                "ecoprovince_mapping": geography.mapping_source,
                "ecoprovince_detail": geography.detail,
                "leading_species": leader,
                "species_match_level": match_level,
                "mapped_ba_share": mapped_share,
                "ba_total_m2ha": composite_prediction.ba_total_m2ha,
                "ba_code_mapped_m2ha": composite_prediction.ba_code_mapped_m2ha,
                "ba_curve_resolved_m2ha": (composite_prediction.ba_curve_resolved_m2ha),
                "ba_species_resolved_m2ha": (
                    composite_prediction.ba_species_resolved_m2ha
                ),
                "ba_group_resolved_m2ha": (composite_prediction.ba_group_resolved_m2ha),
                "ba_code_unmapped_m2ha": (composite_prediction.ba_code_unmapped_m2ha),
                "ba_curve_unresolved_m2ha": (
                    composite_prediction.ba_curve_unresolved_m2ha
                ),
                "curve_resolved_ba_share": (
                    composite_prediction.ba_curve_resolved_m2ha
                    / composite_prediction.ba_total_m2ha
                ),
                "age": age,
                "age_bin": bin_label if bin_label is not None else "outside_bins",
                "observed_volume": float(visit.volume),
                "predicted_leader": predicted_leader,
                "predicted_composite": composite_prediction.predicted_total_ba,
                "predicted_composite_curve_ba": (
                    composite_prediction.predicted_curve_ba
                ),
                "top_height_m": float(visit.top_height_m),
            }
        )

    if not rows:
        raise ValueError("BC benchmark produced no matched visits")

    detail = pd.DataFrame(rows)
    detail["comparison"] = "leading_species"
    detail["predicted_volume"] = detail["predicted_leader"]
    leader_metrics = _bc_summary(detail)
    leader_metrics["comparison"] = "leading_species"

    composite = detail.copy()
    composite["comparison"] = "ba_weighted_composite"
    composite["predicted_volume"] = composite["predicted_composite"]
    composite_metrics = _bc_summary(composite)
    composite_metrics["comparison"] = "ba_weighted_composite"

    summary = pd.concat([leader_metrics, composite_metrics], ignore_index=True)
    summary = summary.sort_values(
        [
            "comparison",
            "ecoprovince",
            "ecoprovince_mapping",
            "leading_species",
            "age_bin",
        ]
    ).reset_index(drop=True)

    stats = {
        "visits_input": len(visits),
        "visits_with_composition": len(ba_by_visit),
        "matched_visits": len(detail),
        "unmatched_visits": int(len(visits) - len(detail)),
        "skip_counts": skip_counts,
        "median_code_mapped_ba_share": float(detail["mapped_ba_share"].median()),
        "median_curve_resolved_ba_share": float(
            detail["curve_resolved_ba_share"].median()
        ),
        "visits_with_curve_unresolved_ba": int(
            (detail["ba_curve_unresolved_m2ha"] > 0).sum()
        ),
        "visits_with_code_unmapped_ba": int(
            (detail["ba_code_unmapped_m2ha"] > 0).sum()
        ),
        "ba_totals_m2ha": {
            "total": float(detail["ba_total_m2ha"].sum()),
            "code_mapped": float(detail["ba_code_mapped_m2ha"].sum()),
            "curve_resolved": float(detail["ba_curve_resolved_m2ha"].sum()),
            "species_resolved": float(detail["ba_species_resolved_m2ha"].sum()),
            "group_resolved": float(detail["ba_group_resolved_m2ha"].sum()),
            "code_unmapped": float(detail["ba_code_unmapped_m2ha"].sum()),
            "curve_unresolved": float(detail["ba_curve_unresolved_m2ha"].sum()),
        },
        "ecoprovince_mapping_sources": detail["ecoprovince_mapping"]
        .value_counts()
        .to_dict(),
        "bec_fallback_zone_counts": fallback_zone_counts,
        "bec_fallback_reason_counts": fallback_reason_counts,
        "age_bins": [f"{low}-{high}" for low, high in BC_AGE_BINS],
        "age_range_used": [min_age, max_age],
    }
    return summary, detail, stats


def _bc_summary(detail: pd.DataFrame) -> pd.DataFrame:
    group_columns = [
        "ecoprovince",
        "ecoprovince_stratum",
        "ecoprovince_mapping",
        "leading_species",
        "age_bin",
    ]
    binned = detail.loc[detail["age_bin"] != "outside_bins"]
    by_bin = summarise_pairs(binned, group_columns)
    overall = summarise_pairs(detail, group_columns[:-1])
    overall["age_bin"] = "all"

    by_ecoprovince = summarise_pairs(
        detail, ["ecoprovince", "ecoprovince_stratum", "ecoprovince_mapping"]
    )
    by_ecoprovince["leading_species"] = "ALL"
    by_ecoprovince["age_bin"] = "all"

    by_ecoprovince_bin = summarise_pairs(
        binned,
        ["ecoprovince", "ecoprovince_stratum", "ecoprovince_mapping", "age_bin"],
    )
    by_ecoprovince_bin["leading_species"] = "ALL"

    by_species = summarise_pairs(detail, ["leading_species"])
    by_species["ecoprovince"] = "ALL"
    by_species["ecoprovince_stratum"] = "ALL"
    by_species["ecoprovince_mapping"] = "ALL"
    by_species["age_bin"] = "all"

    global_by_bin = summarise_pairs(binned, ["ecoprovince_mapping", "age_bin"])
    global_by_bin["ecoprovince"] = "ALL"
    global_by_bin["ecoprovince_stratum"] = "ALL"
    global_by_bin["leading_species"] = "ALL"

    global_by_bin_all = summarise_pairs(binned, ["age_bin"])
    global_by_bin_all["ecoprovince"] = "ALL"
    global_by_bin_all["ecoprovince_stratum"] = "ALL"
    global_by_bin_all["ecoprovince_mapping"] = "ALL"
    global_by_bin_all["leading_species"] = "ALL"

    global_all = summarise_pairs(detail, [])
    global_all["ecoprovince"] = "ALL"
    global_all["ecoprovince_stratum"] = "ALL"
    global_all["ecoprovince_mapping"] = "ALL"
    global_all["leading_species"] = "ALL"
    global_all["age_bin"] = "all"

    combined = pd.concat(
        [
            by_bin,
            overall,
            by_ecoprovince,
            by_ecoprovince_bin,
            by_species,
            global_by_bin,
            global_by_bin_all,
            global_all,
        ],
        ignore_index=True,
    )
    return combined


@dataclass(frozen=True)
class CompositePrediction:
    """BA-weighted RSYC composite with explicit BA accounting.

    ``predicted_total_ba`` divides by all live BA, so code-unmapped or
    curve-unresolved basal area remains in the denominator (it contributes no
    volume).  ``predicted_curve_ba`` renormalises only over BA with a resolved
    curve and is reported separately; it is not the primary comparison.
    """

    predicted_total_ba: float
    predicted_curve_ba: float
    ba_total_m2ha: float
    ba_code_mapped_m2ha: float
    ba_curve_resolved_m2ha: float
    ba_species_resolved_m2ha: float
    ba_group_resolved_m2ha: float
    ba_code_unmapped_m2ha: float
    ba_curve_unresolved_m2ha: float
    n_species_total: int
    n_species_curve_resolved: int


def _composite_prediction(
    lookup: RsycLookup,
    strata_id: str,
    ba_by_species: Mapping[str, float],
    age: float,
) -> CompositePrediction:
    """BA-weighted RSYC prediction over the species mixture.

    Unknown basal area stays in the denominator of the primary composite.  The
    curve-resolved and group-resolved BA totals are returned separately so the
    report never presents a renormalised mapped-only number as the stand BA.
    """

    weighted_sum = 0.0
    ba_total = 0.0
    ba_code_mapped = 0.0
    ba_curve_resolved = 0.0
    ba_species_resolved = 0.0
    ba_group_resolved = 0.0
    ba_code_unmapped = 0.0
    ba_curve_unresolved = 0.0
    n_species_total = 0
    n_species_curve_resolved = 0

    for species, ba in ba_by_species.items():
        ba_value = float(ba)
        if not math.isfinite(ba_value) or ba_value <= 0:
            continue
        ba_total += ba_value
        n_species_total += 1
        rsyc_species = canonical_species_code(species, BC_TO_RSYC)
        if rsyc_species is None:
            ba_code_unmapped += ba_value
            continue
        ba_code_mapped += ba_value
        try:
            curve, level = lookup.curve_for(strata_id, rsyc_species)
        except KeyError:
            ba_curve_unresolved += ba_value
            continue
        value = float(curve.volume(np.array([age], dtype=float))[0])
        if not math.isfinite(value) or value < 0:
            ba_curve_unresolved += ba_value
            continue
        weighted_sum += ba_value * value
        ba_curve_resolved += ba_value
        n_species_curve_resolved += 1
        if level in {"national_fit_cell", "species"}:
            ba_species_resolved += ba_value
        else:
            ba_group_resolved += ba_value

    if ba_total <= 0 or ba_curve_resolved <= 0:
        raise KeyError(f"No curve-resolved species for composite in {strata_id}")
    return CompositePrediction(
        predicted_total_ba=weighted_sum / ba_total,
        predicted_curve_ba=weighted_sum / ba_curve_resolved,
        ba_total_m2ha=ba_total,
        ba_code_mapped_m2ha=ba_code_mapped,
        ba_curve_resolved_m2ha=ba_curve_resolved,
        ba_species_resolved_m2ha=ba_species_resolved,
        ba_group_resolved_m2ha=ba_group_resolved,
        ba_code_unmapped_m2ha=ba_code_unmapped,
        ba_curve_unresolved_m2ha=ba_curve_unresolved,
        n_species_total=n_species_total,
        n_species_curve_resolved=n_species_curve_resolved,
    )


@dataclass(frozen=True)
class QcMix:
    """Validated BFEC conifer/deciduous mix for one curve-age row."""

    status: str
    conifer_share: float | None
    deciduous_share: float | None
    note: str | None = None


def derive_qc_mix(conifer: float, deciduous: float, total: float) -> QcMix:
    """Classify a BFEC row and derive a mix only from a valid positive total.

    Statuses:
    * ``positive_total`` -- finite, non-negative components, balanced total > 0;
    * ``zero_total`` -- finite, non-negative, balanced total == 0;
    * ``invalid_components`` -- non-finite or negative component/total;
    * ``mass_balance_mismatch`` -- conifer + deciduous != total.

    A zero total is never given a 50/50 (or any other) fabricated mix.
    """

    values = (float(conifer), float(deciduous), float(total))
    if not all(math.isfinite(value) for value in values):
        return QcMix("invalid_components", None, None, "non_finite_value")
    conifer_value, deciduous_value, total_value = values
    if min(values) < 0:
        return QcMix("invalid_components", None, None, "negative_value")
    tolerance = QC_MASS_BALANCE_ATOL + QC_MASS_BALANCE_RTOL * abs(total_value)
    if abs(conifer_value + deciduous_value - total_value) > tolerance:
        return QcMix("mass_balance_mismatch", None, None, "components_do_not_sum")
    if total_value == 0:
        return QcMix("zero_total", None, None, "no_observed_volume")
    conifer_share = conifer_value / total_value
    deciduous_share = deciduous_value / total_value
    share_tolerance = QC_MASS_BALANCE_ATOL
    if abs(conifer_share + deciduous_share - 1.0) > share_tolerance:
        return QcMix("mass_balance_mismatch", None, None, "shares_do_not_sum")
    return QcMix("positive_total", conifer_share, deciduous_share)


def build_qc_benchmark(
    curves_path: Path,
    development_types_path: Path,
    lookup: RsycLookup,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Compare Quebec BFEC AAC curves with RSYC conifer/broadleaf components.

    BFEC curves are managed AAC yield tables.  They are filtered to productive
    development types, but even productive tables often flatten at rotation;
    the ``curve_status`` column records that condition so a flat post-rotation
    yield is not mistaken for an unmanaged age trend.
    """

    curves = pd.read_csv(
        curves_path,
        usecols=[
            "unit",
            "development_type",
            "table_id",
            "species_group",
            "age",
            "volume_conifer",
            "volume_deciduous",
            "volume_total",
            "height",
        ],
    )
    development_types = pd.read_csv(
        development_types_path,
        usecols=[
            "unit",
            "dt_key",
            "inventory_area_ha",
            "table_id",
            "in_model",
            "productive",
        ],
    )
    development_types = development_types.loc[
        (development_types["in_model"].astype(str).isin(["1", "True", "true"]))
        & (development_types["productive"].astype(str).isin(["1", "True", "true"]))
        & (development_types["inventory_area_ha"] > 0)
    ]
    if development_types.empty:
        raise ValueError("No productive BFEC development types remain")

    table_area = (
        development_types.groupby(["unit", "table_id"], as_index=False)[
            "inventory_area_ha"
        ]
        .sum()
        .rename(columns={"inventory_area_ha": "curve_area_ha"})
    )
    curves = curves.merge(table_area, on=["unit", "table_id"], how="inner")

    # Determine whether each table's total-volume curve is still rising at
    # the target age (compare with the previous 5-year step).
    previous = curves.loc[:, ["unit", "table_id", "age", "volume_total"]].copy()
    previous["age"] = previous["age"] + 5
    previous = previous.rename(columns={"volume_total": "volume_total_previous"})
    curves = curves.merge(previous, on=["unit", "table_id", "age"], how="left")
    curves["curve_status"] = np.where(
        curves["volume_total_previous"].isna(),
        "no_previous_step",
        np.where(
            np.isclose(
                curves["volume_total"], curves["volume_total_previous"], atol=1e-9
            ),
            "flat_or_post_rotation",
            "rising",
        ),
    )

    target = curves.loc[curves["age"].isin(QC_AGES)].copy()
    if target.empty:
        raise ValueError("No BFEC curve rows matched the target ages")

    pair_records: list[dict[str, object]] = []
    zero_records: list[dict[str, object]] = []
    invalid_records: list[dict[str, object]] = []
    for unit, unit_curves in target.groupby("unit", sort=True):
        region = QC_UNIT_TO_REGION.get(int(unit))
        ecoprovince = QC_REGION_TO_ECOPROVINCE.get(region) if region else None
        if ecoprovince is None:
            continue
        for age, age_rows in unit_curves.groupby("age", sort=True):
            age_int = int(age)
            predicted_conifer = _curve_predict(
                lookup, ecoprovince, "CONIFEROUS", age_int
            )
            predicted_deciduous = _curve_predict(
                lookup, ecoprovince, "BROADLEAF", age_int
            )

            for position, row_index in enumerate(age_rows.index):
                row = age_rows.loc[row_index]
                conifer = float(row["volume_conifer"])
                deciduous = float(row["volume_deciduous"])
                total = float(row["volume_total"])
                mix = derive_qc_mix(conifer, deciduous, total)
                base = {
                    "unit": int(unit),
                    "region": region,
                    "ecoprovince": ecoprovince,
                    "table_id": row["table_id"],
                    "age": age_int,
                    "curve_area_ha": float(row["curve_area_ha"]),
                    "height_m": float(row["height"]),
                    "curve_status": row["curve_status"],
                    "mix_status": mix.status,
                }
                if mix.status in {"invalid_components", "mass_balance_mismatch"}:
                    invalid_records.append(
                        {
                            **base,
                            "volume_conifer": conifer,
                            "volume_deciduous": deciduous,
                            "volume_total": total,
                            "reason": mix.note,
                        }
                    )
                    continue
                if mix.status == "zero_total":
                    zero_records.append(
                        {
                            **base,
                            "volume_conifer": conifer,
                            "volume_deciduous": deciduous,
                            "volume_total": total,
                            "reason": mix.note,
                        }
                    )
                # Component rows are valid for balanced rows even when the
                # total is zero; their hit-rate denominator is published.
                for component, observed, predicted in (
                    ("conifer", conifer, predicted_conifer),
                    ("deciduous", deciduous, predicted_deciduous),
                ):
                    pair_records.append(
                        {
                            **base,
                            "component": component,
                            "observed_volume": float(observed),
                            "predicted_volume": float(predicted),
                        }
                    )
                if mix.status == "positive_total":
                    assert mix.conifer_share is not None
                    assert mix.deciduous_share is not None
                    predicted_total = (
                        mix.conifer_share * predicted_conifer
                        + mix.deciduous_share * predicted_deciduous
                    )
                    pair_records.append(
                        {
                            **base,
                            "component": "total",
                            "observed_volume": float(total),
                            "predicted_volume": float(predicted_total),
                        }
                    )

    if not pair_records:
        raise ValueError("QC benchmark produced no matched curves")
    pairs = pd.DataFrame(pair_records)
    detail_columns = [
        "unit",
        "region",
        "ecoprovince",
        "table_id",
        "age",
        "curve_area_ha",
        "height_m",
        "curve_status",
        "mix_status",
        "volume_conifer",
        "volume_deciduous",
        "volume_total",
        "reason",
    ]
    zero_frame = pd.DataFrame(zero_records, columns=detail_columns)
    invalid_frame = pd.DataFrame(invalid_records, columns=detail_columns)

    unit_rows: list[dict[str, object]] = []
    for (unit, age, component), group in pairs.groupby(
        ["unit", "age", "component"], sort=True
    ):
        metrics = regression_metrics(
            group["observed_volume"].to_numpy(), group["predicted_volume"].to_numpy()
        )
        unit_rows.append(
            {
                "dataset": "QC_BFEC",
                "unit": int(unit),
                "region": group["region"].iloc[0],
                "ecoprovince": group["ecoprovince"].iloc[0],
                "age": int(age),
                "component": component,
                "comparison_basis": (
                    "same_mix_weighted_pure_curves"
                    if component == "total"
                    else "component_vs_pure_curve_not_like_for_like"
                ),
                "n_curves": metrics["n"],
                "n_positive_observed": metrics["n_positive_observed"],
                "n_zero_observed": metrics["n_zero_observed"],
                "within_20pct_denominator": metrics["n_positive_observed"],
                "mean_observed": metrics["mean_observed"],
                "area_weighted_observed": area_weighted_mean(
                    group["curve_area_ha"].to_numpy(),
                    group["observed_volume"].to_numpy(),
                ),
                "mean_height_m": float(group["height_m"].mean()),
                "area_weighted_height_m": area_weighted_mean(
                    group["curve_area_ha"].to_numpy(), group["height_m"].to_numpy()
                ),
                "share_flat_curves": float(
                    (group["curve_status"] == "flat_or_post_rotation").mean()
                ),
                "predicted_rsyc": float(group["predicted_volume"].mean()),
                "bias": metrics["bias"],
                "mae": metrics["mae"],
                "rmse": metrics["rmse"],
                "relative_bias": metrics["relative_bias"],
                "relative_rmse": metrics["relative_rmse"],
                "correlation": metrics["correlation"],
                "within_20pct": metrics["within_20pct"],
                "within_30pct": metrics["within_30pct"],
            }
        )

    global_rows: list[dict[str, object]] = []
    for (age, component), group in pairs.groupby(["age", "component"], sort=True):
        metrics = regression_metrics(
            group["observed_volume"].to_numpy(), group["predicted_volume"].to_numpy()
        )
        global_rows.append(
            {
                "dataset": "QC_BFEC",
                "unit": "ALL",
                "region": "ALL",
                "ecoprovince": "ALL",
                "age": int(age),
                "component": component,
                "comparison_basis": (
                    "same_mix_weighted_pure_curves"
                    if component == "total"
                    else "component_vs_pure_curve_not_like_for_like"
                ),
                "n_curves": metrics["n"],
                "n_positive_observed": metrics["n_positive_observed"],
                "n_zero_observed": metrics["n_zero_observed"],
                "within_20pct_denominator": metrics["n_positive_observed"],
                "mean_observed": metrics["mean_observed"],
                "area_weighted_observed": area_weighted_mean(
                    group["curve_area_ha"].to_numpy(),
                    group["observed_volume"].to_numpy(),
                ),
                "mean_height_m": float(group["height_m"].mean()),
                "area_weighted_height_m": area_weighted_mean(
                    group["curve_area_ha"].to_numpy(), group["height_m"].to_numpy()
                ),
                "share_flat_curves": float(
                    (group["curve_status"] == "flat_or_post_rotation").mean()
                ),
                "predicted_rsyc": float(group["predicted_volume"].mean()),
                "bias": metrics["bias"],
                "mae": metrics["mae"],
                "rmse": metrics["rmse"],
                "relative_bias": metrics["relative_bias"],
                "relative_rmse": metrics["relative_rmse"],
                "correlation": metrics["correlation"],
                "within_20pct": metrics["within_20pct"],
                "within_30pct": metrics["within_30pct"],
            }
        )

    summary = (
        pd.DataFrame([*unit_rows, *global_rows])
        .sort_values(["unit", "age", "component"])
        .reset_index(drop=True)
    )

    stats = {
        "units": sorted(target["unit"].unique().tolist()),
        "unit_ecoprovince": {
            str(int(unit)): QC_REGION_TO_ECOPROVINCE.get(
                QC_UNIT_TO_REGION.get(int(unit))
            )
            for unit in target["unit"].unique()
        },
        "curves_at_target_ages": int(
            target[["unit", "table_id", "age"]].drop_duplicates().shape[0]
        ),
        "productive_curves": int(
            target[["unit", "table_id"]].drop_duplicates().shape[0]
        ),
        "ages": list(QC_AGES),
        "total_positive_rows": int((pairs["component"] == "total").sum()),
        "zero_total_rows": len(zero_frame),
        "invalid_mix_rows": len(invalid_frame),
        "excluded_total_rows": int(len(zero_frame) + len(invalid_frame)),
        "zero_total_by_age": {
            str(int(age)): int(count)
            for age, count in zero_frame.groupby("age").size().items()
        }
        if not zero_frame.empty
        else {},
        "invalid_mix_by_age": {
            str(int(age)): int(count)
            for age, count in invalid_frame.groupby("age").size().items()
        }
        if not invalid_frame.empty
        else {},
        "zero_total_tables": int(zero_frame["table_id"].nunique())
        if not zero_frame.empty
        else 0,
        "mix_rule": (
            "A total-volume prediction is emitted only when components are "
            "finite, non-negative, sum to total, and total > 0. Zero-total "
            "rows are a separate stratum; invalid/mismatched rows are "
            "excluded. No fabricated mix is used."
        ),
        "flat_curve_share_by_age": {
            str(int(age)): float(
                (group["curve_status"] == "flat_or_post_rotation").mean()
            )
            for age, group in target.groupby("age")
        },
        "bfec_caveat": (
            "BFEC curves are AAC/wood-supply model outputs with treatment "
            "themes, not unmanaged observations; productive tables are kept, "
            "but many flatten at rotation, so absolute levels are policy and "
            "management assumptions, not independent ground truth. RSYC has "
            "no height-age model, so BFEC height is reported for context only."
        ),
    }
    return summary, pairs, zero_frame, invalid_frame, stats


def _curve_predict(lookup: RsycLookup, strata_id: str, species: str, age: int) -> float:
    value, _ = lookup.predict(strata_id, species, float(age))
    return value


def build_mgm_benchmark(
    mgm_dir: Path,
    national_area_weights_path: Path,
    lookup: RsycLookup,
    stratum_by_ecoprovince: Mapping[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Compare Lochhead MGM western-boreal yield groups with RSYC."""

    area_weights = pd.read_csv(
        national_area_weights_path,
        usecols=["province", "ecozone", "ecoprovince", "rsyc_species", "area_ha"],
    )
    area_weights["stratum_id"] = (
        area_weights["ecozone"].astype(str)
        + "/"
        + area_weights["ecoprovince"].astype(str)
    )
    province_species_weights = area_weights.groupby(
        ["province", "rsyc_species", "stratum_id"], as_index=False
    )["area_ha"].sum()

    rows: list[dict[str, object]] = []
    curve_rows: list[dict[str, object]] = []
    for province in ("AB", "SK", "MB", "BC"):
        path = mgm_dir / f"{province.lower()}_gy.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing MGM file: {path}")
        frame = pd.read_csv(
            path,
            usecols=[
                "Age",
                "stavHt",
                "EstStCC",
                "DenseClass",
                "ygrp",
                "c_a",
                "c_b",
                "c_c",
                "d_a",
                "d_b",
                "d_c",
            ],
        )
        frame["parsed"] = frame["ygrp"].map(parse_mgm_ygrp)
        parsed = pd.DataFrame(frame["parsed"].tolist(), index=frame.index)
        frame = pd.concat([frame.drop(columns=["parsed"]), parsed], axis=1)
        frame = frame.loc[frame["species_code"].isin(MGM_TO_RSYC)].copy()
        if frame.empty:
            continue
        frame["rsyc_species"] = frame["species_code"].map(MGM_TO_RSYC)
        frame["conifer_volume"] = _mgm_component(
            frame, "c_a", "c_b", "c_c", frame["Age"]
        )
        frame["deciduous_volume"] = _mgm_component(
            frame, "d_a", "d_b", "d_c", frame["Age"]
        )
        frame["total_volume"] = frame["conifer_volume"] + frame["deciduous_volume"]
        curve_library = frame.drop_duplicates("ygrp").copy()
        target = _mgm_target_rows(frame)

        for (species_code, rsyc_species, age), group in target.groupby(
            ["species_code", "rsyc_species", "Age"], sort=True
        ):
            weights, weight_basis = _province_species_weights(
                province_species_weights, province, rsyc_species
            )
            if not weights:
                continue
            predicted = _province_prediction(lookup, weights, rsyc_species, int(age))
            observed = group["total_volume"].to_numpy(dtype=float)
            metrics = regression_metrics(observed, np.full_like(observed, predicted))
            rows.append(
                {
                    "dataset": "MGM",
                    "province": province,
                    "mgm_species": species_code,
                    "rsyc_species": rsyc_species,
                    "comparison": "volume_at_age",
                    "age": int(age),
                    "age_basis": "fixed_yield_group_at_age",
                    "n_curves": len(group),
                    "n_starting_age_cohorts": int(group["starting_age"].nunique()),
                    "starting_age_min": int(group["starting_age"].min()),
                    "starting_age_max": int(group["starting_age"].max()),
                    "starting_age_stratum": (
                        f"{int(group['starting_age'].min())}-"
                        f"{int(group['starting_age'].max())}"
                    ),
                    "age_offset_min": int(group["age_offset"].min()),
                    "age_offset_max": int(group["age_offset"].max()),
                    "n_positive_observed": metrics["n_positive_observed"],
                    "within_20pct_denominator": metrics["n_positive_observed"],
                    "mgm_mean_volume": float(np.mean(observed)),
                    "mgm_median_volume": float(np.median(observed)),
                    "mgm_p10_volume": float(np.quantile(observed, 0.10)),
                    "mgm_p90_volume": float(np.quantile(observed, 0.90)),
                    "rsyc_area_weighted_volume": predicted,
                    "bias": metrics["bias"],
                    "mae": metrics["mae"],
                    "rmse": metrics["rmse"],
                    "relative_bias": metrics["relative_bias"],
                    "relative_rmse": metrics["relative_rmse"],
                    "within_20pct": metrics["within_20pct"],
                    "within_30pct": metrics["within_30pct"],
                    "median_top_height_m": float(group["stavHt"].median()),
                    "p10_top_height_m": float(group["stavHt"].quantile(0.10)),
                    "p90_top_height_m": float(group["stavHt"].quantile(0.90)),
                    "median_crown_closure_pct": float(group["EstStCC"].median()),
                    "rsyc_top_height_m": math.nan,
                    "height_note": "RSYC catalogue has no height-age model; top height is MGM-reported only.",
                    "mgm_median_mai_age": math.nan,
                    "rsyc_mai_age": math.nan,
                    "mai_age_difference": math.nan,
                    "weight_basis": weight_basis,
                    "equation_note": MGM_EQUATION_NOTE,
                }
            )
            curve_rows.extend(
                _mgm_curve_rows(province, species_code, rsyc_species, group)
            )

        # MAI endpoint maximum per province/species.  MGM curves are native to
        # 0-350 years; a maximum at the 350-year endpoint is right-censored, not
        # a culmination age.
        for (species_code, rsyc_species), group in curve_library.groupby(
            ["species_code", "rsyc_species"], sort=True
        ):
            weights, weight_basis = _province_species_weights(
                province_species_weights, province, rsyc_species
            )
            if not weights:
                continue
            mgm_mai_ages, mgm_mai_status = _mgm_mai_endpoint(group)
            rsyc_mai_age = _province_mai_age(lookup, weights, rsyc_species)
            status_counts = pd.Series(mgm_mai_status).value_counts().to_dict()
            rows.append(
                {
                    "dataset": "MGM",
                    "province": province,
                    "mgm_species": species_code,
                    "rsyc_species": rsyc_species,
                    "comparison": "mai_endpoint_max",
                    "age": math.nan,
                    "age_basis": "native_0_350_curve",
                    "n_curves": len(group),
                    "mgm_mean_volume": math.nan,
                    "mgm_median_volume": math.nan,
                    "mgm_p10_volume": math.nan,
                    "mgm_p90_volume": math.nan,
                    "rsyc_area_weighted_volume": math.nan,
                    "bias": math.nan,
                    "mae": math.nan,
                    "rmse": math.nan,
                    "relative_bias": math.nan,
                    "relative_rmse": math.nan,
                    "within_20pct": math.nan,
                    "within_30pct": math.nan,
                    "median_top_height_m": math.nan,
                    "p10_top_height_m": math.nan,
                    "p90_top_height_m": math.nan,
                    "median_crown_closure_pct": math.nan,
                    "rsyc_top_height_m": math.nan,
                    "height_note": "RSYC catalogue has no height-age model; top height is MGM-reported only.",
                    "mgm_median_mai_age": float(np.median(mgm_mai_ages)),
                    "mgm_mai_status": (
                        "interior_max"
                        if set(mgm_mai_status) == {"interior_max"}
                        else "endpoint_censored"
                        if all(
                            status.startswith(("left_censored", "right_censored"))
                            for status in mgm_mai_status
                        )
                        else "mixed_endpoint_status"
                    ),
                    "mgm_share_right_censored": float(
                        np.mean(
                            [
                                status == "right_censored_at_native_max"
                                for status in mgm_mai_status
                            ]
                        )
                    ),
                    "mgm_mai_status_counts": status_counts,
                    "rsyc_mai_age": int(rsyc_mai_age),
                    "rsyc_mai_status": (
                        "right_censored_at_150"
                        if int(rsyc_mai_age) >= RSYC_MAX_OPERATIONAL_AGE
                        else "interior_max"
                    ),
                    "mai_age_difference": float(rsyc_mai_age - np.median(mgm_mai_ages)),
                    "mai_comparison_note": (
                        "MGM ages are native endpoint maxima (censored at 350); "
                        "RSYC is truncated at its operational 150-year limit."
                    ),
                    "weight_basis": weight_basis,
                    "equation_note": MGM_EQUATION_NOTE,
                }
            )

    if not rows:
        raise ValueError("MGM benchmark produced no matched province/species/age rows")
    summary = (
        pd.DataFrame(rows)
        .sort_values(["province", "rsyc_species", "age"])
        .reset_index(drop=True)
    )
    detail = pd.DataFrame(curve_rows)
    stats = {
        "provinces": sorted(summary["province"].unique().tolist()),
        "species": sorted(summary["rsyc_species"].unique().tolist()),
        "rows": len(summary),
        "equation": MGM_EQUATION,
        "equation_note": MGM_EQUATION_NOTE,
        "equation_verification": {
            "status": "verified",
            "repository": MGM_SOURCE_REPOSITORY,
            "commit": MGM_SOURCE_COMMIT,
            "commit_sha": "184787233aaa761f7b3eda54155d9dbbe50bf779",
            "file": MGM_SOURCE_FILE,
            "file_sha256": "2b591065c25869a48d4a396edfb2502e78013ae44228abcb6847a2ecd3eea590",
            "function": MGM_SOURCE_FUNCTION,
            "expression": "a * pow(x,b) * math.exp(-c*x)",
            "age_origin": 0,
            "age_units": "years",
            "native_max_age": MGM_NATIVE_MAX_AGE,
            "period_length": MGM_PERIOD_LENGTH,
            "total_volume": "cvol + dvol via _SUM",
        },
        "cohort_rule": (
            "Fixed yield group evaluated at each requested age inside its "
            "stored 31-year window; starting age is retained as a covariate, "
            "not required to equal the evaluation age."
        ),
        "mai_rule": (
            "MAI endpoint maximum over native 0-350-year support; maxima at "
            "350 are right-censored. RSYC comparison is truncated at 150."
        ),
        "vph": "VPH is empty in the pinned CSVs and was not used.",
    }
    return summary, detail, stats


def _mgm_component(
    frame: pd.DataFrame, a: str, b: str, c: str, ages: pd.Series
) -> np.ndarray:
    ages_array = ages.to_numpy(dtype=float)
    return (
        frame[a].to_numpy(dtype=float)
        * np.power(ages_array, frame[b].to_numpy(dtype=float))
        * np.exp(-frame[c].to_numpy(dtype=float) * ages_array)
    )


def _province_species_weights(
    weights: pd.DataFrame, province: str, rsyc_species: str
) -> tuple[dict[str, float], str]:
    subset = weights.loc[
        (weights["province"] == province) & (weights["rsyc_species"] == rsyc_species)
    ]
    if not subset.empty:
        return dict(
            zip(subset["stratum_id"], subset["area_ha"], strict=True)
        ), "species_area"
    group = "BROADLEAF" if rsyc_species in BROADLEAF_SPECIES else "CONIFEROUS"
    subset = weights.loc[
        (weights["province"] == province) & (weights["rsyc_species"] == group)
    ]
    if subset.empty:
        return {}, "none"
    return dict(
        zip(subset["stratum_id"], subset["area_ha"], strict=True)
    ), f"{group.lower()}_area"


def _province_prediction(
    lookup: RsycLookup,
    weights: Mapping[str, float],
    rsyc_species: str,
    age: int,
) -> float:
    predictions: list[float] = []
    areas: list[float] = []
    for stratum_id, area in weights.items():
        if area <= 0:
            continue
        try:
            value, _ = lookup.predict(stratum_id, rsyc_species, float(age))
        except KeyError:
            continue
        predictions.append(value)
        areas.append(float(area))
    if not predictions:
        raise KeyError(f"No RSYC prediction for {rsyc_species} at age {age}")
    return area_weighted_mean(areas, predictions)


def _province_mai_age(
    lookup: RsycLookup,
    weights: Mapping[str, float],
    rsyc_species: str,
    max_age: int = RSYC_MAX_OPERATIONAL_AGE,
) -> int:
    """Age of maximum mean annual increment for an area-weighted RSYC curve."""

    ages = np.arange(RSYC_MIN_AGE, max_age + 1, dtype=float)
    volumes = np.array(
        [_province_prediction(lookup, weights, rsyc_species, int(age)) for age in ages]
    )
    mai = volumes / ages
    return int(ages[int(np.argmax(mai))])


def _mgm_target_rows(
    frame: pd.DataFrame, ages: Sequence[int] = MGM_AGES
) -> pd.DataFrame:
    """Select fixed yield groups at the requested ages.

    The parsed ``age`` is the starting age encoded in ``ygrp``; it is not a
    filter on ``Age``.  A yield group is evaluated at every requested age
    inside its stored 31-year window, and the starting-age offset is retained
    so cohort effects remain visible.
    """

    target = frame.loc[frame["Age"].isin(ages)].copy()
    if target.empty:
        return target
    target["starting_age"] = target["age"].astype(int)
    target["age_offset"] = target["Age"].astype(int) - target["starting_age"]
    target = target.loc[target["age_offset"] >= 0].copy()
    return target


def _mgm_mai_endpoint(
    frame: pd.DataFrame, max_age: int = MGM_NATIVE_MAX_AGE
) -> tuple[np.ndarray, np.ndarray]:
    """Return MAI endpoint age and censoring status for each MGM curve.

    MGM coefficients are native to 0-350 years.  An argmax at the final age is
    a right-censored endpoint, not a demonstrated culmination.
    """

    ages = np.arange(RSYC_MIN_AGE, max_age + 1, dtype=float)
    conifer = (
        frame["c_a"].to_numpy(dtype=float)[:, None]
        * np.power(ages[None, :], frame["c_b"].to_numpy(dtype=float)[:, None])
        * np.exp(-frame["c_c"].to_numpy(dtype=float)[:, None] * ages[None, :])
    )
    deciduous = (
        frame["d_a"].to_numpy(dtype=float)[:, None]
        * np.power(ages[None, :], frame["d_b"].to_numpy(dtype=float)[:, None])
        * np.exp(-frame["d_c"].to_numpy(dtype=float)[:, None] * ages[None, :])
    )
    mai = (conifer + deciduous) / ages[None, :]
    endpoint_ages = ages[np.argmax(mai, axis=1)].astype(int)
    status = np.where(
        endpoint_ages >= max_age,
        "right_censored_at_native_max",
        np.where(endpoint_ages <= RSYC_MIN_AGE, "left_censored_at_1", "interior_max"),
    )
    return endpoint_ages, status


def _mgm_curve_rows(
    province: str, species_code: str, rsyc_species: str, group: pd.DataFrame
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for curve in group.itertuples(index=False):
        rows.append(
            {
                "province": province,
                "mgm_species": species_code,
                "rsyc_species": rsyc_species,
                "ygrp": curve.ygrp,
                "age": int(curve.Age),
                "stavHt": float(curve.stavHt),
                "EstStCC": float(curve.EstStCC),
                "DenseClass": curve.DenseClass,
                "conifer_volume": float(curve.conifer_volume),
                "deciduous_volume": float(curve.deciduous_volume),
                "total_volume": float(curve.total_volume),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_inputs(repo_root: Path) -> dict[str, Path]:
    return {
        "rsyc_models": repo_root / "data/raw/national/rsyc/RSYC_models.zip",
        "national_fit": repo_root
        / "data/derived/national/assortments/national_fit/national_distribution_parameters.csv",
        "national_area_weights": repo_root
        / "data/derived/national/assortments/national_area_weights/national_area_weights_ecodistrict.csv",
        "bc_visits": repo_root
        / "data/derived/fvsca_calibration/bc_faib_fvsbc_schema/visits.csv",
        "bc_trees": repo_root / "data/derived/fvsca_calibration/v0/trees.csv.gz",
        "bc_area_weights": repo_root
        / "data/derived/national/assortments/bc_area_weights/bc_area_weights.csv",
        "qc_curves": repo_root
        / "data/derived/national/qc_bfec_sample/qc_bfec_curves.csv",
        "qc_development_types": repo_root
        / "data/derived/national/qc_bfec_sample/qc_bfec_development_types.csv",
    }


def _fit_level_factors(
    frame: pd.DataFrame,
    stratum_columns: Sequence[str],
    observed_column: str,
    predicted_column: str,
    min_train_rows: int,
) -> pd.DataFrame:
    """Fit multiplicative level factors on training rows only."""

    if frame.empty:
        return pd.DataFrame(columns=[*stratum_columns, "factor", "n_train"])
    grouper = stratum_columns[0] if len(stratum_columns) == 1 else list(stratum_columns)
    records: list[dict[str, object]] = []
    for key, group in frame.groupby(grouper, dropna=False, sort=False):
        observed = group[observed_column].to_numpy(dtype=float)
        predicted = group[predicted_column].to_numpy(dtype=float)
        valid = (
            np.isfinite(observed)
            & np.isfinite(predicted)
            & (observed >= 0)
            & (predicted > 0)
        )
        if int(valid.sum()) < min_train_rows:
            continue
        denominator = float(predicted[valid].sum())
        if denominator <= 0:
            continue
        factor = float(observed[valid].sum()) / denominator
        if not math.isfinite(factor) or factor <= 0:
            continue
        key_values = (key,) if len(stratum_columns) == 1 else key
        record = dict(zip(stratum_columns, key_values, strict=True))
        record.update(
            {
                "factor": factor,
                "n_train": int(valid.sum()),
                "train_observed": float(observed[valid].sum()),
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def _level_holdout(
    detail: pd.DataFrame,
    *,
    unit_column: str,
    stratum_columns: Sequence[str],
    min_train_rows: int = 20,
    observed_column: str = "observed_volume",
    predicted_column: str = "predicted_volume",
) -> dict[str, object]:
    """Leave-one-unit-out evaluation of level multipliers.

    Every unit is held out once; factors are fitted on the other units in the
    same stratum.  This is a holdout proxy, not a substitute for the
    predeclared whole-unit split in the planning protocol.
    """

    if detail.empty or unit_column not in detail.columns:
        return {"evaluable": False, "reason": "missing unit column or rows"}
    fold_records: list[dict[str, object]] = []
    for unit in sorted(detail[unit_column].dropna().unique().tolist()):
        holdout = detail.loc[detail[unit_column] == unit].copy()
        train = detail.loc[detail[unit_column] != unit]
        factors = _fit_level_factors(
            train,
            stratum_columns,
            observed_column,
            predicted_column,
            min_train_rows,
        )
        if factors.empty:
            continue
        merged = holdout.merge(factors, on=list(stratum_columns), how="inner")
        if merged.empty:
            continue
        observed = merged[observed_column].to_numpy(dtype=float)
        predicted = merged[predicted_column].to_numpy(dtype=float)
        factor = merged["factor"].to_numpy(dtype=float)
        valid = np.isfinite(observed) & np.isfinite(predicted) & np.isfinite(factor)
        if not valid.any():
            continue
        observed = observed[valid]
        before = predicted[valid]
        after = before * factor[valid]
        observed_sum = float(observed.sum())
        if observed_sum <= 0:
            continue
        fold_records.append(
            {
                "unit": str(unit),
                "n_rows": int(observed.size),
                "observed_sum": observed_sum,
                "bias_before": float((before.sum() - observed_sum) / observed_sum),
                "bias_after": float((after.sum() - observed_sum) / observed_sum),
                "rmse_before": float(np.sqrt(np.mean(np.square(before - observed)))),
                "rmse_after": float(np.sqrt(np.mean(np.square(after - observed)))),
            }
        )

    if not fold_records:
        return {
            "evaluable": False,
            "reason": "no holdout unit had a training stratum with enough rows",
        }
    folds = pd.DataFrame(fold_records)
    observed_sum = float(folds["observed_sum"].sum())
    predicted_before_sum = float(
        (folds["observed_sum"] * (1.0 + folds["bias_before"])).sum()
    )
    predicted_after_sum = float(
        (folds["observed_sum"] * (1.0 + folds["bias_after"])).sum()
    )
    bias_before = (
        (predicted_before_sum - observed_sum) / observed_sum
        if observed_sum > 0
        else math.nan
    )
    bias_after = (
        (predicted_after_sum - observed_sum) / observed_sum
        if observed_sum > 0
        else math.nan
    )

    before_abs = folds["bias_before"].abs()
    after_abs = folds["bias_after"].abs()
    return {
        "evaluable": True,
        "protocol": "leave_one_unit_out_level_multiplier",
        "unit_column": unit_column,
        "stratum_columns": list(stratum_columns),
        "min_train_rows": min_train_rows,
        "n_units_evaluable": len(folds),
        "n_holdout_rows": int(folds["n_rows"].sum()),
        "observed_sum": observed_sum,
        "bias_before": bias_before,
        "bias_after": bias_after,
        "median_unit_abs_bias_before": float(before_abs.median()),
        "median_unit_abs_bias_after": float(after_abs.median()),
        "share_units_improved": float((after_abs < before_abs).mean()),
        "share_units_materially_improved": float(
            ((after_abs < before_abs) & (before_abs - after_abs > 0.05)).mean()
        ),
        "folds": fold_records,
    }


def _build_correction_candidates(
    bc_summary: pd.DataFrame,
    qc_summary: pd.DataFrame,
    bc_holdout: Mapping[str, object],
    qc_holdout: Mapping[str, object],
) -> pd.DataFrame:
    """Build a candidate-only, withheld correction table."""

    records: list[dict[str, object]] = []
    bc_candidates = bc_summary.loc[
        (bc_summary["comparison"] == "ba_weighted_composite")
        & (bc_summary["age_bin"] == "all")
        & (bc_summary["ecoprovince"] != "ALL")
        & (bc_summary["leading_species"] != "ALL")
        & (bc_summary["n"] >= 30)
    ]
    for row in bc_candidates.itertuples(index=False):
        relative_bias = float(row.relative_bias)
        if not math.isfinite(relative_bias) or abs(relative_bias) <= 0.20:
            continue
        denominator = 1.0 + relative_bias
        multiplier = 1.0 / denominator if denominator > 0 else math.nan
        records.append(
            {
                "correction_key": f"{row.ecoprovince_stratum}|{row.leading_species}",
                "dataset": "BC_FAIB",
                "ecoprovince": row.ecoprovince,
                "ecoprovince_stratum": row.ecoprovince_stratum,
                "species": row.leading_species,
                "age_basis": "all_ages_20_150",
                "multiplier": multiplier,
                "n": int(row.n),
                "relative_bias": relative_bias,
                "holdout_protocol": "leave_one_tsa_out_level_multiplier",
                "holdout_evaluable": bool(bc_holdout.get("evaluable", False)),
                "holdout_share_units_improved": bc_holdout.get("share_units_improved"),
                "holdout_bias_before": bc_holdout.get("bias_before"),
                "holdout_bias_after": bc_holdout.get("bias_after"),
                "status": "withheld",
                "release_decision": "not_released_pending_predeclared_holdout",
            }
        )

    qc_candidates = qc_summary.loc[
        (qc_summary["unit"] != "ALL")
        & (qc_summary["age"] == 50)
        & (qc_summary["component"] == "total")
        & (qc_summary["n_curves"] >= 10)
    ]
    for row in qc_candidates.itertuples(index=False):
        relative_bias = float(row.relative_bias)
        if not math.isfinite(relative_bias) or abs(relative_bias) <= 0.20:
            continue
        denominator = 1.0 + relative_bias
        multiplier = 1.0 / denominator if denominator > 0 else math.nan
        records.append(
            {
                "correction_key": f"QC_BFEC|{row.unit}|total|age50",
                "dataset": "QC_BFEC",
                "ecoprovince": row.ecoprovince,
                "ecoprovince_stratum": row.ecoprovince,
                "species": "total",
                "age_basis": "age_50",
                "multiplier": multiplier,
                "n": int(row.n_curves),
                "relative_bias": relative_bias,
                "holdout_protocol": "leave_one_unit_out_level_multiplier",
                "holdout_evaluable": bool(qc_holdout.get("evaluable", False)),
                "holdout_share_units_improved": qc_holdout.get("share_units_improved"),
                "holdout_bias_before": qc_holdout.get("bias_before"),
                "holdout_bias_after": qc_holdout.get("bias_after"),
                "status": "withheld",
                "release_decision": "not_released_pending_predeclared_holdout",
            }
        )
    return pd.DataFrame(records)


def run_benchmark(
    *,
    repo_root: Path,
    out_dir: Path,
    mgm_dir: Path,
) -> dict[str, object]:
    inputs = _default_inputs(repo_root)
    for name, path in inputs.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing input {name}: {path}")

    models = load_rsyc_models(inputs["rsyc_models"])
    cell_models = load_cell_models(inputs["national_fit"])
    lookup = RsycLookup(models, cell_models)

    bc_summary, bc_detail, bc_stats = build_bc_benchmark(
        inputs["bc_visits"],
        inputs["bc_trees"],
        inputs["bc_area_weights"],
        inputs["national_area_weights"],
        lookup,
    )
    qc_summary, qc_detail, qc_zero, qc_invalid, qc_stats = build_qc_benchmark(
        inputs["qc_curves"],
        inputs["qc_development_types"],
        lookup,
    )
    mgm_summary, mgm_detail, mgm_stats = build_mgm_benchmark(
        mgm_dir,
        inputs["national_area_weights"],
        lookup,
        ecoprovince_stratum_map(inputs["national_area_weights"]),
    )

    bc_holdout = _level_holdout(
        bc_detail,
        unit_column="tsa",
        stratum_columns=["ecoprovince_stratum", "leading_species"],
    )
    qc_holdout = _level_holdout(
        qc_detail.loc[qc_detail["component"] == "total"],
        unit_column="unit",
        stratum_columns=["component"],
    )
    holdout = {
        "status": "evaluated_but_not_sufficient_to_release_factors",
        "protocol_reference": "planning/national-rsyc-backbone.md section 5.2",
        "bc": bc_holdout,
        "qc": qc_holdout,
        "mgm": {
            "evaluable": False,
            "reason": (
                "pinned MGM CSVs have no FMU/polygon key; a whole-FMU "
                "holdout cannot be constructed"
            ),
        },
        "release_rule": (
            "Do not release a correction unless it generalises across held-out "
            "whole units, has the same sign in most folds, and is material "
            "against benchmark uncertainty."
        ),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    bc_summary.to_csv(out_dir / "rsyc_vs_bc.csv", index=False)
    qc_summary.to_csv(out_dir / "rsyc_vs_qc.csv", index=False)
    mgm_summary.to_csv(out_dir / "rsyc_vs_mgm.csv", index=False)
    bc_detail.to_csv(out_dir / "rsyc_vs_bc_visits.csv", index=False)
    qc_detail.to_csv(out_dir / "rsyc_vs_qc_curves.csv", index=False)
    qc_zero.to_csv(out_dir / "rsyc_vs_qc_zero_total.csv", index=False)
    qc_invalid.to_csv(out_dir / "rsyc_vs_qc_invalid_mix.csv", index=False)
    mgm_detail.to_csv(out_dir / "rsyc_vs_mgm_curves.csv", index=False)

    correction_candidates = _build_correction_candidates(
        bc_summary, qc_summary, bc_holdout, qc_holdout
    )
    correction_candidates.to_csv(out_dir / "local_correction_factors.csv", index=False)

    summary = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "exploratory_volume_only",
        "claim": (
            "Volume-only benchmark. AGB, height, taper, and grade were not "
            "validated; no AGB backbone claim is made."
        ),
        "rsyc_curves_available": {
            "national_ecoprovince_volume_curves": int(
                (models["strata_level"] == "ecoprovince").sum()
            ),
            "national_ecozone_fallback_curves": int(
                (models["strata_level"] == "ecozone").sum()
            ),
            "species_curves": int(
                (
                    (models["model_group"] == "species")
                    & (models["strata_level"] == "ecoprovince")
                ).sum()
            ),
            "coniferous_group_curves": int(
                (
                    (models["model_group"] == "coniferous")
                    & (models["strata_level"] == "ecoprovince")
                ).sum()
            ),
            "broadleaf_group_curves": int(
                (
                    (models["model_group"] == "broadleaf")
                    & (models["strata_level"] == "ecoprovince")
                ).sum()
            ),
            "generic_group_curves": int(
                (
                    (models["model_group"] == "generic")
                    & (models["strata_level"] == "ecoprovince")
                ).sum()
            ),
            "ecoprovinces": int(
                models.loc[
                    models["strata_level"] == "ecoprovince", "strata_id"
                ].nunique()
            ),
            "operational_age_range": [RSYC_MIN_AGE, RSYC_MAX_OPERATIONAL_AGE],
            "attributes": [
                "total volume (m3/ha)",
                "above-ground biomass (not used here)",
            ],
        },
        "bc": bc_stats,
        "qc": qc_stats,
        "mgm": mgm_stats,
        "holdout": holdout,
        "correction_factors": {
            "status": "withheld",
            "reason": (
                "The planning protocol requires a predeclared whole-unit "
                "holdout before release. The benchmark now evaluates a "
                "leave-one-unit-out proxy, but it is not sufficient to "
                "release factors; MGM has no FMU key and QC has only five "
                "units. All numeric candidates are withheld."
            ),
            "candidate_rows": len(correction_candidates),
            "file": "local_correction_factors.csv",
        },
        "overall": _overall_findings(bc_summary, qc_summary, mgm_summary),
    }
    (out_dir / "benchmark_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    manifest = {
        "generated_utc": summary["generated_utc"],
        "inputs": {
            name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for name, path in inputs.items()
        }
        | {
            f"mgm_{province.lower()}": {
                "path": str(mgm_dir / f"{province.lower()}_gy.csv"),
                "bytes": (mgm_dir / f"{province.lower()}_gy.csv").stat().st_size,
                "sha256": file_sha256(mgm_dir / f"{province.lower()}_gy.csv"),
            }
            for province in ("AB", "SK", "MB", "BC")
        },
        "outputs": {
            path.name: {"bytes": path.stat().st_size}
            for path in sorted(out_dir.glob("*"))
            if path.is_file()
            and path.suffix in {".csv", ".json", ".md", ".png"}
            and path.name not in {"manifest.json"}
        },
        "row_counts": {
            "bc_summary": len(bc_summary),
            "bc_detail": len(bc_detail),
            "qc_summary": len(qc_summary),
            "qc_detail": len(qc_detail),
            "qc_zero_total": len(qc_zero),
            "qc_invalid_mix": len(qc_invalid),
            "mgm_summary": len(mgm_summary),
            "mgm_detail": len(mgm_detail),
            "correction_candidates": len(correction_candidates),
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return summary


def _overall_findings(
    bc: pd.DataFrame, qc: pd.DataFrame, mgm: pd.DataFrame
) -> dict[str, object]:
    """Extract compact, defensible headline statistics for the JSON summary."""

    bc_global = bc.loc[
        (bc["ecoprovince"] == "ALL")
        & (bc["leading_species"] == "ALL")
        & (bc["age_bin"] == "all")
    ]
    qc_total = qc.loc[(qc["unit"] == "ALL") & (qc["component"] == "total")]
    mgm_volume = mgm.loc[mgm["comparison"] == "volume_at_age"]
    mgm_mai = mgm.loc[mgm["comparison"] == "mai_endpoint_max"]
    return {
        "bc_leading_species": _headline(
            bc_global.loc[bc_global["comparison"] == "leading_species"]
        ),
        "bc_composite": _headline(
            bc_global.loc[bc_global["comparison"] == "ba_weighted_composite"]
        ),
        "qc_total": _headline(qc_total),
        "mgm_volume": _headline(mgm_volume, weight_column="n_curves"),
        "mgm_mai_endpoint_max": {
            "rows": len(mgm_mai),
            "median_mai_age_mgm": float(mgm_mai["mgm_median_mai_age"].median()),
            "median_mai_age_rsyc": float(mgm_mai["rsyc_mai_age"].median()),
            "median_age_difference": float(mgm_mai["mai_age_difference"].median()),
            "share_mgm_endpoint_right_censored": float(
                mgm_mai["mgm_share_right_censored"].mean()
            ),
            "status_note": (
                "MGM maxima at the 350-year native endpoint are right-censored; "
                "do not report them as culmination ages."
            ),
        },
    }


def _headline(
    frame: pd.DataFrame, weight_column: str | None = None
) -> dict[str, float | int]:
    if frame.empty:
        return {"rows": 0}
    if weight_column and weight_column in frame.columns:
        weights = frame[weight_column].to_numpy(dtype=float)
        bias = float(np.average(frame["bias"], weights=weights))
        rel_bias = float(np.average(frame["relative_bias"].abs(), weights=weights))
    else:
        bias = float(frame["bias"].mean())
        rel_bias = float(frame["relative_bias"].abs().mean())
    return {
        "rows": len(frame),
        "median_abs_relative_bias": float(frame["relative_bias"].abs().median()),
        "mean_abs_relative_rmse": float(frame["relative_rmse"].abs().mean()),
        "share_within_20pct": float(frame["within_20pct"].mean()),
        "share_within_30pct": float(frame["within_30pct"].mean()),
        "mean_bias_m3ha": bias,
        "mean_abs_relative_bias": rel_bias,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/derived/national/rsyc_benchmark"),
    )
    parser.add_argument("--mgm-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    mgm_dir = args.mgm_dir or resolve_external_root(repo_root)
    if not mgm_dir.exists():
        print(f"MGM directory not found: {mgm_dir}", file=sys.stderr)
        return 2
    summary = run_benchmark(repo_root=repo_root, out_dir=args.out_dir, mgm_dir=mgm_dir)
    print(json.dumps({k: v for k, v in summary.items() if k != "overall"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
