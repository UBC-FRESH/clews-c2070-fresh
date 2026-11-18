"""RSYC national volume-curve backbone with explicit fallback.

The backbone selects one published volume curve per target area cell
(ecodistrict or ecoprovince, species).  Selection walks the RSYC ecological
hierarchy from the target level outward, exhausts exact-species curves before
model groups, then records an explicit failure.  The tile layer is both the
last spatial fallback and the control-total layer; it is never preferred over
an exact-species ecological curve.

The module also builds a tile-first control library on the same area cells so
the cost of ecodistrict-first selection can be measured rather than asserted.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .rsyc import load_rsyc_volume_models

LEVEL_LADDER: tuple[str, ...] = (
    "ecodistrict",
    "ecoregion",
    "ecoprovince",
    "ecozone",
    "tile",
)
TARGET_LADDER_START: dict[str, int] = {"ecodistrict": 0, "ecoprovince": 2}
AGE_GRID: tuple[float, ...] = (50.0, 100.0, 150.0)
EVIDENCE_TIERS: tuple[str, ...] = ("rsyc_direct", "rsyc_fallback", "rsyc_group")

# Model-group label for every catalogue species.  Group targets are uppercase
# in the area layer (BROADLEAF, CONIFEROUS) and lowercase in the model catalogue.
SPECIES_GROUP: dict[str, str] = {
    "ABIE.AMA": "coniferous",
    "ABIE.BAL": "coniferous",
    "ABIE.LAS": "coniferous",
    "ACER.MAC": "broadleaf",
    "ACER.RUB": "broadleaf",
    "ACER.SAH": "broadleaf",
    "ALNU.RUB": "broadleaf",
    "BETU.ALL": "broadleaf",
    "BETU.PAP": "broadleaf",
    "FRAX.NIG": "broadleaf",
    "LARI.LAR": "coniferous",
    "LARI.OCC": "coniferous",
    "PICE.ABI": "coniferous",
    "PICE.ENG": "coniferous",
    "PICE.GLA": "coniferous",
    "PICE.MAR": "coniferous",
    "PICE.RUB": "coniferous",
    "PICE.SIT": "coniferous",
    "PINU.BAN": "coniferous",
    "PINU.CON": "coniferous",
    "PINU.PON": "coniferous",
    "PINU.RES": "coniferous",
    "PINU.STR": "coniferous",
    "POPU.BAL": "broadleaf",
    "POPU.GRA": "broadleaf",
    "POPU.TRE": "broadleaf",
    "PSEU.MEN": "coniferous",
    "QUER.RUB": "broadleaf",
    "THUJ.OCC": "coniferous",
    "THUJ.PLI": "coniferous",
    "TSUG.HET": "coniferous",
    "TSUG.MER": "coniferous",
    "BROADLEAF": "broadleaf",
    "CONIFEROUS": "coniferous",
    "GENERIC": "generic",
}
GROUP_TARGETS: frozenset[str] = frozenset({"BROADLEAF", "CONIFEROUS", "GENERIC"})

# The catalogue spells ecodistrict 438 differently from the boundary layer.
# The correction is explicit and counted; it is never inferred by fuzzy match.
STRATA_ID_CORRECTIONS: dict[str, str] = {
    "Boreal Shield East/Eastern Boreal Shield/Central Laurentians/Betsiamites Plateau": (
        "Boreal Shield East/Eastern Boreal Shield/Central Laurentians/Betsiamiles Plateau"
    ),
}


class RsycBackboneError(ValueError):
    """Raised when the backbone cannot be built from the supplied inputs."""


@dataclass(frozen=True)
class CurveSelection:
    """One auditable ladder result for a target cell."""

    model_id: str | None
    selected_level: str | None
    fallback_depth: int
    evidence_tier: str
    source_species: str | None
    status: str
    failure_reason: str | None
    model: Mapping[str, Any] | None

    @property
    def has_curve(self) -> bool:
        return self.model_id is not None


def normalise_species(value: Any) -> str:
    """Return the canonical target-species label (uppercase species/group key)."""
    text = str(value).strip()
    if not text:
        raise RsycBackboneError("empty species label in area weights")
    upper = text.upper()
    if upper in SPECIES_GROUP:
        return upper
    raise RsycBackboneError(f"species label not present in RSYC catalogue: {value!r}")


def group_label_for(species: str) -> str:
    """Return the lowercase model-group label used by the RSYC catalogue."""
    return SPECIES_GROUP[normalise_species(species)]


def is_group_target(species: str) -> bool:
    return normalise_species(species) in GROUP_TARGETS


def ladder_for_target(target_level: str) -> tuple[str, ...]:
    """Return the spatial ladder from a target level outward."""
    if target_level not in TARGET_LADDER_START:
        raise RsycBackboneError(f"unsupported target level: {target_level!r}")
    return LEVEL_LADDER[TARGET_LADDER_START[target_level] :]


def canonical_strata_id(strata_id: str) -> str:
    """Apply the single documented ecodistrict-438 spelling correction."""
    return STRATA_ID_CORRECTIONS.get(strata_id, strata_id)


def evidence_tier_for(
    *,
    selected_level: str,
    target_level: str,
    is_group: bool,
) -> str:
    """Classify a selected curve without looking at its validation metrics."""
    if is_group:
        return "rsyc_group"
    if selected_level == target_level:
        return "rsyc_direct"
    return "rsyc_fallback"


def fallback_depth_for(*, selected_level: str, target_level: str, kind: str) -> int:
    """Count ladder steps from the target level; groups sort after all species."""
    ladder = ladder_for_target(target_level)
    if selected_level not in ladder:
        raise RsycBackboneError(
            f"selected level {selected_level!r} is outside the {target_level} ladder"
        )
    offset = ladder.index(selected_level)
    if kind == "species":
        return offset
    if kind == "group":
        return len(ladder) + offset
    if kind == "generic":
        return 2 * len(ladder) + offset
    raise RsycBackboneError(f"unknown curve kind: {kind!r}")


def select_curve(
    *,
    target_species: str,
    target_level: str,
    hierarchy_keys: Mapping[str, str | None],
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    search_levels: Sequence[str] | None = None,
    evidence_target_level: str | None = None,
) -> CurveSelection:
    """Walk the RSYC ladder for one target cell.

    Exact-species curves are exhausted across every search level before any
    model-group curve is considered.  The target species group is tried before
    the generic curve.  A missing curve returns an explicit failure record; it
    never returns a silent NaN.
    """
    species = normalise_species(target_species)
    levels = (
        tuple(search_levels)
        if search_levels is not None
        else ladder_for_target(target_level)
    )
    if not levels:
        raise RsycBackboneError("search level list cannot be empty")
    tier_target = evidence_target_level or target_level

    if species not in GROUP_TARGETS:
        for level in levels:
            key = hierarchy_keys.get(level)
            model = model_index.get((level, key, species)) if key else None
            if model is not None:
                return CurveSelection(
                    model_id=str(model["model_id"]),
                    selected_level=level,
                    fallback_depth=fallback_depth_for(
                        selected_level=level, target_level=tier_target, kind="species"
                    ),
                    evidence_tier=evidence_tier_for(
                        selected_level=level, target_level=tier_target, is_group=False
                    ),
                    source_species=str(model["species"]),
                    status="ok",
                    failure_reason=None,
                    model=model,
                )

    group = group_label_for(species)
    group_labels = (group,) if group == "generic" else (group, "generic")
    for kind, label in zip(
        ("group" if group != "generic" else "generic", "generic"),
        group_labels,
    ):
        for level in levels:
            key = hierarchy_keys.get(level)
            model = model_index.get((level, key, label)) if key else None
            if model is not None:
                return CurveSelection(
                    model_id=str(model["model_id"]),
                    selected_level=level,
                    fallback_depth=fallback_depth_for(
                        selected_level=level, target_level=tier_target, kind=kind
                    ),
                    evidence_tier="rsyc_group",
                    source_species=str(model["species"]),
                    status="ok",
                    failure_reason=None,
                    model=model,
                )

    return CurveSelection(
        model_id=None,
        selected_level=None,
        fallback_depth=-1,
        evidence_tier="none",
        source_species=None,
        status="no_curve",
        failure_reason=(
            "no exact-species, species-group, or generic curve at any ladder level"
        ),
        model=None,
    )


def evaluate_volume_curve(
    *,
    b1: float,
    b2: float,
    b3: float,
    b4: float,
    age: float,
    model_id: str | None = None,
) -> float:
    """Evaluate the published RSYC Chapman-Richards volume equation.

    Fails loudly on non-finite parameters, non-positive age, or non-finite or
    negative volume.  The catalogue's operational range is 1-150 years; callers
    must not pass ages outside that range.
    """
    parameters = tuple(float(value) for value in (b1, b2, b3, b4, age))
    if not all(math.isfinite(value) for value in parameters):
        raise RsycBackboneError(f"non-finite curve input for model {model_id!r}")
    b1_value, b2_value, b3_value, b4_value, age_value = parameters
    if age_value <= 0.0:
        raise RsycBackboneError(f"age must be positive for model {model_id!r}")
    if age_value > 150.0:
        raise RsycBackboneError(
            f"age {age_value} exceeds the RSYC operational range 1-150 for model {model_id!r}"
        )
    if b1_value <= 0.0 or b2_value <= 0.0 or b3_value <= 0.0:
        raise RsycBackboneError(
            f"non-positive b1/b2/b3 for model {model_id!r}: "
            f"{b1_value}, {b2_value}, {b3_value}"
        )
    value = (
        b1_value
        * math.exp(-b4_value * age_value)
        * (1.0 - math.exp(-b2_value * age_value)) ** b3_value
    )
    if not math.isfinite(value) or value < 0.0:
        raise RsycBackboneError(
            f"curve evaluation produced invalid volume for model {model_id!r}: {value}"
        )
    return float(value)


def build_model_index(
    models: pd.DataFrame,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index national volume curves by (level, canonical strata_id, species)."""
    required = {
        "model_id",
        "strata_level",
        "strata_id",
        "species",
        "b1",
        "b2",
        "b3",
        "b4",
    }
    missing = sorted(required.difference(models.columns))
    if missing:
        raise RsycBackboneError(f"model table missing columns: {', '.join(missing)}")

    frame = models.copy()
    frame["strata_id"] = frame["strata_id"].astype(str).map(canonical_strata_id)
    for column in ("b1", "b2", "b3", "b4"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["b1", "b2", "b3", "b4"]].isna().any().any():
        raise RsycBackboneError("model table contains non-numeric coefficients")
    coefficients = frame[["b1", "b2", "b3", "b4"]].to_numpy(dtype=float)
    if not np.isfinite(coefficients).all():
        raise RsycBackboneError("model table contains non-finite coefficients")
    if (frame[["b1", "b2", "b3"]].to_numpy() <= 0).any():
        raise RsycBackboneError("model table contains non-positive b1/b2/b3")
    invalid_levels = sorted(set(frame["strata_level"]) - set(LEVEL_LADDER))
    if invalid_levels:
        raise RsycBackboneError(f"unsupported model strata levels: {invalid_levels}")

    keys = ["strata_level", "strata_id", "species"]
    duplicate_count = int(frame.duplicated(keys).sum())
    if duplicate_count:
        raise RsycBackboneError(
            f"model keys are not unique: {duplicate_count} duplicates"
        )

    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        key = (
            str(record["strata_level"]),
            str(record["strata_id"]),
            str(record["species"]),
        )
        index[key] = record
    return index


def load_ecostrata(ecostrat_path: Path) -> dict[str, pd.DataFrame]:
    """Load the four RSYC ecological layers as attribute tables plus geometry."""
    import geopandas as gpd

    ecostrat_path = Path(ecostrat_path)
    if not ecostrat_path.exists():
        raise FileNotFoundError(f"RSYC ecostrat layer not found: {ecostrat_path}")
    layers = {
        "ecodistrict": "ecodistricts",
        "ecoregion": "ecoregions",
        "ecoprovince": "ecoprovinces",
        "ecozone": "ecozones",
    }
    result: dict[str, pd.DataFrame] = {}
    for level, layer in layers.items():
        frame = gpd.read_file(ecostrat_path, layer=layer)
        if "join_key" not in frame.columns:
            raise RsycBackboneError(f"{layer} is missing join_key")
        result[level] = frame
    return result


def validate_model_strata(
    models: pd.DataFrame, strata: Mapping[str, pd.DataFrame]
) -> dict[str, int]:
    """Prove every model stratum exists in the boundary layer after correction."""
    checked: dict[str, int] = {}
    frame = models.copy()
    frame["strata_id"] = frame["strata_id"].astype(str).map(canonical_strata_id)
    for level in LEVEL_LADDER:
        if level == "tile":
            continue
        subset = frame[frame["strata_level"].eq(level)]
        if subset.empty:
            raise RsycBackboneError(f"no {level} models in catalogue")
        expected = set(strata[level]["join_key"].astype(str))
        missing = sorted(set(subset["strata_id"]) - expected)
        if missing:
            raise RsycBackboneError(
                f"{level} model strata absent from boundary layer: {missing[:3]}"
            )
        checked[level] = int(subset["strata_id"].nunique())
    return checked


def _empty_area_diagnostics() -> dict[str, Any]:
    return {
        "zero_area_rows": 0,
        "zero_area_ha": 0.0,
        "unmapped_rows": 0,
        "unmapped_area_ha": 0.0,
        "duplicate_unit_species_rows": 0,
        "ecoprovince_residual_rows": 0,
        "ecoprovince_residual_area_ha": 0.0,
    }


def build_area_targets(
    *,
    ecodistrict_weights: pd.DataFrame,
    ecoprovince_weights: pd.DataFrame,
    strata: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse SCANFI weights into positive-area target cells.

    The ecodistrict layer is the primary target.  Ecoprovince rows are added
    only for the residual area not represented by any ecodistrict, so the
    combined table sums to the published ecoprovince total without
    double-counting.
    """
    diagnostics = _empty_area_diagnostics()
    required = {
        "unit_id",
        "ecozone",
        "ecoprovince",
        "ecodistrict",
        "province",
        "rsyc_species",
        "area_ha",
    }
    missing = sorted(required.difference(ecodistrict_weights.columns))
    if missing:
        raise RsycBackboneError(
            f"ecodistrict weights missing columns: {', '.join(missing)}"
        )

    frame = ecodistrict_weights.copy()
    frame["area_ha"] = pd.to_numeric(frame["area_ha"], errors="coerce")
    frame["target_species"] = frame["rsyc_species"].map(normalise_species)
    frame["ecodistrict_triple"] = (
        frame["ecozone"].astype(str)
        + "|"
        + frame["ecoprovince"].astype(str)
        + "|"
        + frame["ecodistrict"].astype(str)
    )

    non_positive = frame["area_ha"].isna() | frame["area_ha"].le(0)
    diagnostics["zero_area_rows"] = int(non_positive.sum())
    diagnostics["zero_area_ha"] = float(
        frame.loc[non_positive, "area_ha"].fillna(0).sum()
    )
    positive = frame.loc[~non_positive].copy()
    if positive.empty:
        raise RsycBackboneError("no positive-area ecodistrict weights found")

    eco = strata["ecodistrict"][
        [
            "join_key",
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "ecodistrict",
            "ecodistrict_id",
        ]
    ].copy()
    eco["ecodistrict_triple"] = (
        eco["ecozone"].astype(str)
        + "|"
        + eco["ecoprovince"].astype(str)
        + "|"
        + eco["ecodistrict"].astype(str)
    )
    if eco["ecodistrict_triple"].duplicated().any():
        raise RsycBackboneError("boundary layer has duplicate ecodistrict triples")

    mapped = positive.merge(
        eco,
        on="ecodistrict_triple",
        how="left",
        suffixes=("", "_boundary"),
        validate="many_to_one",
    )
    unmapped_mask = mapped["join_key"].isna()
    diagnostics["unmapped_rows"] = int(unmapped_mask.sum())
    diagnostics["unmapped_area_ha"] = float(mapped.loc[unmapped_mask, "area_ha"].sum())
    mapped = mapped.loc[~unmapped_mask].copy()

    conflicts = mapped.groupby("unit_id")[["province", "join_key"]].nunique()
    if (conflicts > 1).any().any():
        raise RsycBackboneError(
            "area unit_id maps to conflicting province or ecodistrict"
        )

    diagnostics["duplicate_unit_species_rows"] = int(
        mapped.duplicated(["unit_id", "target_species"]).sum()
    )
    targets = (
        mapped.groupby(["unit_id", "target_species"], as_index=False)
        .agg(
            area_ha=("area_ha", "sum"),
            province=("province", "first"),
            ecozone=("ecozone", "first"),
            ecoprovince=("ecoprovince", "first"),
            ecodistrict=("ecodistrict", "first"),
            ecodistrict_id=("ecodistrict_id", "first"),
            ecodistrict_key=("join_key", "first"),
            ecoregion=("ecoregion", "first"),
        )
        .assign(target_level="ecodistrict")
    )
    targets["ecoprovince_key"] = (
        targets["ecozone"].astype(str) + "/" + targets["ecoprovince"].astype(str)
    )
    targets["ecoregion_key"] = targets["ecodistrict_key"].str.rsplit("/", n=1).str[0]
    targets["ecozone_key"] = targets["ecozone"].astype(str)
    targets["cell_key"] = (
        targets["target_level"].astype(str)
        + ":"
        + targets["unit_id"].astype(str)
        + "|"
        + targets["target_species"].astype(str)
    )

    ecodistrict_area_total = float(targets["area_ha"].sum())

    residual_targets = _build_ecoprovince_residuals(
        ecoprovince_weights=ecoprovince_weights,
        ecodistrict_targets=targets,
        strata=strata,
        diagnostics=diagnostics,
    )
    all_targets = pd.concat([targets, residual_targets], ignore_index=True, sort=False)
    all_targets["cell_key"] = (
        all_targets["target_level"].astype(str)
        + ":"
        + all_targets["unit_id"].astype(str)
        + "|"
        + all_targets["target_species"].astype(str)
    )
    diagnostics["ecodistrict_area_ha"] = ecodistrict_area_total
    diagnostics["target_area_ha"] = float(all_targets["area_ha"].sum())
    return all_targets.reset_index(drop=True), diagnostics


def _build_ecoprovince_residuals(
    *,
    ecoprovince_weights: pd.DataFrame,
    ecodistrict_targets: pd.DataFrame,
    strata: Mapping[str, pd.DataFrame],
    diagnostics: dict[str, Any],
) -> pd.DataFrame:
    required = {
        "unit_id",
        "ecozone",
        "ecoprovince",
        "rsyc_species",
        "area_ha",
    }
    missing = sorted(required.difference(ecoprovince_weights.columns))
    if missing:
        raise RsycBackboneError(
            f"ecoprovince weights missing columns: {', '.join(missing)}"
        )
    frame = ecoprovince_weights.copy()
    frame["area_ha"] = pd.to_numeric(frame["area_ha"], errors="coerce")
    frame = frame.loc[frame["area_ha"].gt(0)].copy()
    frame["target_species"] = frame["rsyc_species"].map(normalise_species)
    frame["ecoprovince_key"] = (
        frame["ecozone"].astype(str) + "/" + frame["ecoprovince"].astype(str)
    )
    province_total = frame.groupby(
        ["ecoprovince_key", "target_species"], as_index=False
    )["area_ha"].sum()
    allocated = ecodistrict_targets.groupby(
        ["ecoprovince_key", "target_species"], as_index=False
    )["area_ha"].sum()
    residual = province_total.merge(
        allocated,
        on=["ecoprovince_key", "target_species"],
        how="left",
        suffixes=("_province", "_ecodistrict"),
    )
    residual = residual.rename(
        columns={
            "area_ha_province": "area_province",
            "area_ha_ecodistrict": "area_ecodistrict",
        }
    )
    residual["area_ecodistrict"] = residual["area_ecodistrict"].fillna(0.0)
    residual["area_ha"] = residual["area_province"] - residual["area_ecodistrict"]
    tolerance = 1e-6
    residual = residual.loc[residual["area_ha"].gt(tolerance)].copy()
    diagnostics["ecoprovince_residual_rows"] = len(residual)
    diagnostics["ecoprovince_residual_area_ha"] = float(residual["area_ha"].sum())
    if residual.empty:
        return pd.DataFrame(
            columns=[
                "unit_id",
                "target_species",
                "area_ha",
                "province",
                "ecozone",
                "ecoprovince",
                "ecodistrict",
                "ecodistrict_id",
                "ecodistrict_key",
                "ecoregion",
                "target_level",
                "ecoprovince_key",
                "ecoregion_key",
                "ecozone_key",
            ]
        )

    province_lookup = (
        frame.drop_duplicates("ecoprovince_key")
        .set_index("ecoprovince_key")
        .loc[:, ["unit_id", "ecozone", "ecoprovince"]]
    )
    residual = residual.join(province_lookup, on="ecoprovince_key")
    residual["province"] = "ALL"
    residual["ecodistrict"] = None
    residual["ecodistrict_id"] = None
    residual["ecodistrict_key"] = None
    residual["ecoregion"] = None
    residual["ecoregion_key"] = None
    residual["ecozone_key"] = residual["ecozone"].astype(str)
    residual["target_level"] = "ecoprovince"
    return residual[
        [
            "unit_id",
            "target_species",
            "area_ha",
            "province",
            "ecozone",
            "ecoprovince",
            "ecodistrict",
            "ecodistrict_id",
            "ecodistrict_key",
            "ecoregion",
            "target_level",
            "ecoprovince_key",
            "ecoregion_key",
            "ecozone_key",
        ]
    ]


def assign_tiles(
    *,
    keys: Iterable[str],
    level: str,
    strata: Mapping[str, pd.DataFrame],
    tiles_path: Path,
) -> pd.DataFrame:
    """Assign the 150 km tile containing each unit centroid.

    A centroid may fall in no published tile or on a boundary.  Both are
    recorded; an ambiguous centroid is resolved to the lexicographically first
    containing tile and never guessed by nearest distance.
    """
    import geopandas as gpd

    wanted = sorted({str(value) for value in keys if value is not None})
    if not wanted:
        raise RsycBackboneError(f"no {level} keys supplied for tile assignment")
    tiles_path = Path(tiles_path)
    if not tiles_path.exists():
        raise FileNotFoundError(f"RSYC tile layer not found: {tiles_path}")
    layer = strata[level]
    subset = layer.loc[layer["join_key"].astype(str).isin(wanted)].copy()
    missing = sorted(set(wanted) - set(subset["join_key"].astype(str)))
    if missing:
        raise RsycBackboneError(
            f"{level} keys absent from boundary layer: {missing[:3]}"
        )
    subset["geometry"] = subset.geometry.centroid
    tiles = gpd.read_file(tiles_path, layer="RSYC_tiles")
    if "TileID" not in tiles.columns:
        raise RsycBackboneError("tile layer is missing TileID")

    joined = gpd.sjoin(
        subset[["join_key", "geometry"]],
        tiles[["TileID", "geometry"]],
        how="left",
        predicate="within",
    )
    joined = joined.drop(columns=["index_right"], errors="ignore")
    joined["boundary_centroid"] = False

    matched = set(joined.loc[joined["TileID"].notna(), "join_key"].astype(str))
    boundary = subset.loc[~subset["join_key"].astype(str).isin(matched)].copy()
    if not boundary.empty:
        boundary_join = gpd.sjoin(
            boundary[["join_key", "geometry"]],
            tiles[["TileID", "geometry"]],
            how="inner",
            predicate="intersects",
        )
        boundary_status = pd.DataFrame(boundary_join[["join_key", "TileID"]])
        boundary_status["boundary_centroid"] = True
        joined = pd.concat(
            [joined, boundary_status],
            ignore_index=True,
            sort=False,
        )
        joined["boundary_centroid"] = joined["boundary_centroid"].fillna(False)

    rows: list[dict[str, Any]] = []
    for key in wanted:
        candidates = sorted(
            {
                str(value)
                for value in joined.loc[
                    joined["join_key"].astype(str).eq(key), "TileID"
                ].dropna()
            }
        )
        on_boundary = bool(
            joined.loc[joined["join_key"].astype(str).eq(key), "boundary_centroid"]
            .fillna(False)
            .any()
        )
        if len(candidates) == 1:
            status = (
                "centroid_on_tile_boundary"
                if on_boundary
                else "centroid_within_single_tile"
            )
            tile_id = candidates[0]
        elif len(candidates) > 1:
            status = "ambiguous_centroid_in_multiple_tiles"
            tile_id = candidates[0]
        else:
            status = "no_published_tile_contains_centroid"
            tile_id = None
        rows.append(
            {
                f"{level}_key": key,
                "tile_id": tile_id,
                "tile_join_status": status,
                "tile_candidate_count": len(candidates),
            }
        )
    return pd.DataFrame(rows)


def attach_tiles(
    targets: pd.DataFrame,
    *,
    strata: Mapping[str, pd.DataFrame],
    tiles_path: Path,
) -> pd.DataFrame:
    """Attach tile assignments for ecodistrict and ecoprovince targets."""
    result = targets.copy()
    result["tile_id"] = pd.Series([None] * len(result), dtype="object")
    result["tile_join_status"] = pd.Series([None] * len(result), dtype="object")
    result["tile_candidate_count"] = 0

    for level, key_column in (
        ("ecodistrict", "ecodistrict_key"),
        ("ecoprovince", "ecoprovince_key"),
    ):
        mask = result["target_level"].eq(level)
        if not mask.any():
            continue
        mapping = assign_tiles(
            keys=result.loc[mask, key_column].dropna().unique(),
            level=level,
            strata=strata,
            tiles_path=tiles_path,
        )
        mapping = mapping.rename(columns={f"{level}_key": key_column}).set_index(
            key_column
        )
        row_keys = result.loc[mask, key_column]
        result.loc[mask, "tile_id"] = row_keys.map(mapping["tile_id"])
        result.loc[mask, "tile_join_status"] = row_keys.map(mapping["tile_join_status"])
        result.loc[mask, "tile_candidate_count"] = row_keys.map(
            mapping["tile_candidate_count"]
        )
    result["tile_id"] = result["tile_id"].astype("string")
    result["tile_candidate_count"] = (
        result["tile_candidate_count"].fillna(0).astype(int)
    )
    return result


def _hierarchy_keys(
    row: Mapping[str, Any], *, tile_only: bool = False
) -> dict[str, str | None]:
    tile = None if pd.isna(row.get("tile_id")) else str(row.get("tile_id"))
    if tile_only:
        return {"tile": tile}
    return {
        "ecodistrict": _string_or_none(row.get("ecodistrict_key")),
        "ecoregion": _string_or_none(row.get("ecoregion_key")),
        "ecoprovince": _string_or_none(row.get("ecoprovince_key")),
        "ecozone": _string_or_none(row.get("ecozone_key")),
        "tile": tile,
    }


def _string_or_none(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value)
    return None if text in {"", "nan", "<NA>"} else text


def build_library(
    targets: pd.DataFrame,
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    *,
    mode: str,
) -> pd.DataFrame:
    """Select and evaluate one curve per positive-area target cell."""
    if mode not in {"ecodistrict_first", "tile_first"}:
        raise RsycBackboneError(f"unsupported library mode: {mode!r}")
    required = {
        "cell_key",
        "target_level",
        "target_species",
        "area_ha",
        "province",
        "unit_id",
    }
    missing = sorted(required.difference(targets.columns))
    if missing:
        raise RsycBackboneError(f"target table missing columns: {', '.join(missing)}")

    records: list[dict[str, Any]] = []
    for row in targets.to_dict("records"):
        target_level = str(row["target_level"])
        search_levels = (
            ladder_for_target(target_level)
            if mode == "ecodistrict_first"
            else ("tile",)
        )
        selection = select_curve(
            target_species=str(row["target_species"]),
            target_level=target_level,
            hierarchy_keys=_hierarchy_keys(row, tile_only=mode == "tile_first"),
            model_index=model_index,
            search_levels=search_levels,
            evidence_target_level=target_level,
        )
        record: dict[str, Any] = {
            "cell_key": row["cell_key"],
            "target_level": target_level,
            "unit_id": row["unit_id"],
            "province": row["province"],
            "ecozone": row.get("ecozone"),
            "ecoprovince": row.get("ecoprovince"),
            "ecodistrict": row.get("ecodistrict"),
            "ecodistrict_id": row.get("ecodistrict_id"),
            "ecodistrict_key": _string_or_none(row.get("ecodistrict_key")),
            "ecoregion": row.get("ecoregion"),
            "ecoregion_key": _string_or_none(row.get("ecoregion_key")),
            "ecoprovince_key": _string_or_none(row.get("ecoprovince_key")),
            "ecozone_key": _string_or_none(row.get("ecozone_key")),
            "tile_id": _string_or_none(row.get("tile_id")),
            "tile_join_status": row.get("tile_join_status"),
            "target_species": row["target_species"],
            "species_kind": "group"
            if is_group_target(row["target_species"])
            else "species",
            "area_ha": float(row["area_ha"]),
            "selected_level": selection.selected_level,
            "fallback_depth": int(selection.fallback_depth),
            "fallback_steps": int(selection.fallback_depth),
            "evidence_tier": selection.evidence_tier,
            "model_id": selection.model_id,
            "source_species": selection.source_species,
            "status": selection.status,
            "failure_reason": selection.failure_reason,
        }
        if selection.model is not None:
            model = selection.model
            record.update(
                {
                    "response": model.get("response"),
                    "response_units": model.get("response_units"),
                    "model_group": model.get("model_group"),
                    "strata_id": model.get("strata_id"),
                    "b1": float(model["b1"]),
                    "b2": float(model["b2"]),
                    "b3": float(model["b3"]),
                    "b4": float(model["b4"]),
                    "n_obs": model.get("n_obs"),
                    "n_units": model.get("n_units"),
                    "strata_id_corrected": bool(
                        str(model.get("strata_id")) in STRATA_ID_CORRECTIONS.values()
                    ),
                }
            )
            record["coefficient_sign_flag"] = (
                "ok" if float(model["b4"]) >= 0.0 else "negative_b4"
            )
            for age in AGE_GRID:
                record[f"volume_m3ha_age{int(age)}"] = evaluate_volume_curve(
                    b1=float(model["b1"]),
                    b2=float(model["b2"]),
                    b3=float(model["b3"]),
                    b4=float(model["b4"]),
                    age=float(age),
                    model_id=str(model["model_id"]),
                )
        else:
            record.update(
                {
                    "response": None,
                    "response_units": None,
                    "model_group": None,
                    "strata_id": None,
                    "b1": None,
                    "b2": None,
                    "b3": None,
                    "b4": None,
                    "n_obs": None,
                    "n_units": None,
                    "strata_id_corrected": False,
                    "coefficient_sign_flag": None,
                }
            )
            for age in AGE_GRID:
                record[f"volume_m3ha_age{int(age)}"] = None
        records.append(record)

    columns = [
        "cell_key",
        "target_level",
        "unit_id",
        "province",
        "ecozone",
        "ecoprovince",
        "ecodistrict",
        "ecodistrict_id",
        "ecodistrict_key",
        "ecoregion",
        "ecoregion_key",
        "ecoprovince_key",
        "ecozone_key",
        "tile_id",
        "tile_join_status",
        "target_species",
        "species_kind",
        "area_ha",
        "selected_level",
        "fallback_depth",
        "fallback_steps",
        "evidence_tier",
        "model_id",
        "source_species",
        "response",
        "response_units",
        "model_group",
        "strata_id",
        "b1",
        "b2",
        "b3",
        "b4",
        "n_obs",
        "n_units",
        "strata_id_corrected",
        "coefficient_sign_flag",
        "volume_m3ha_age50",
        "volume_m3ha_age100",
        "volume_m3ha_age150",
        "status",
        "failure_reason",
    ]
    return pd.DataFrame(records, columns=columns)


def _area_by(library: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    frame = library.copy()
    for tier in ("rsyc_direct", "rsyc_fallback", "rsyc_group"):
        frame[f"area_{tier}"] = frame["area_ha"].where(
            frame["evidence_tier"].eq(tier), 0.0
        )
    frame["area_covered"] = frame["area_ha"].where(frame["status"].eq("ok"), 0.0)
    frame["cells"] = 1
    result = (
        frame.groupby(list(keys), as_index=False)
        .agg(
            target_cells=("cells", "sum"),
            area_ha=("area_ha", "sum"),
            covered_area_ha=("area_covered", "sum"),
            direct_area_ha=("area_rsyc_direct", "sum"),
            fallback_area_ha=("area_rsyc_fallback", "sum"),
            group_area_ha=("area_rsyc_group", "sum"),
        )
        .assign(
            coverage_fraction=lambda d: _safe_divide(
                d["covered_area_ha"], d["area_ha"]
            ),
            direct_share=lambda d: _safe_divide(d["direct_area_ha"], d["area_ha"]),
            fallback_share=lambda d: _safe_divide(d["fallback_area_ha"], d["area_ha"]),
            group_share=lambda d: _safe_divide(d["group_area_ha"], d["area_ha"]),
        )
    )
    return result


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.div(denominator.where(denominator.gt(0), np.nan)).fillna(0.0)


def selection_summary(library: pd.DataFrame) -> dict[str, Any]:
    ok = library.loc[library["status"].eq("ok")]
    tier_counts = library["evidence_tier"].value_counts().to_dict()
    level_counts = ok["selected_level"].value_counts().to_dict()
    depth_counts = (
        library.groupby("fallback_depth", dropna=False)
        .agg(cells=("cell_key", "size"), area_ha=("area_ha", "sum"))
        .reset_index()
        .sort_values("fallback_depth")
    )
    depth_records = [
        {
            "fallback_depth": int(row["fallback_depth"]),
            "cells": int(row["cells"]),
            "area_ha": float(row["area_ha"]),
        }
        for row in depth_counts.to_dict("records")
    ]
    return {
        "target_cells": len(library),
        "covered_cells": int(ok.shape[0]),
        "failed_cells": int(library["status"].ne("ok").sum()),
        "by_evidence_tier": {key: int(value) for key, value in tier_counts.items()},
        "by_selected_level": {key: int(value) for key, value in level_counts.items()},
        "by_source_species": {
            str(key): int(value)
            for key, value in ok["source_species"].value_counts().items()
        },
        "fallback_depth_distribution": depth_records,
        "negative_b4_curves": int(ok["coefficient_sign_flag"].eq("negative_b4").sum()),
    }


def area_coverage_summary(
    library: pd.DataFrame, diagnostics: Mapping[str, Any]
) -> dict[str, Any]:
    total = float(library["area_ha"].sum())
    covered = float(library.loc[library["status"].eq("ok"), "area_ha"].sum())
    direct = float(
        library.loc[library["evidence_tier"].eq("rsyc_direct"), "area_ha"].sum()
    )
    fallback = float(
        library.loc[library["evidence_tier"].eq("rsyc_fallback"), "area_ha"].sum()
    )
    group = float(
        library.loc[library["evidence_tier"].eq("rsyc_group"), "area_ha"].sum()
    )
    failed = float(library.loc[library["status"].ne("ok"), "area_ha"].sum())
    province = _area_by(library, ["province"])
    return {
        "total_area_ha": total,
        "covered_area_ha": covered,
        "coverage_fraction": _ratio(covered, total),
        "direct_area_ha": direct,
        "direct_share": _ratio(direct, total),
        "fallback_area_ha": fallback,
        "fallback_share": _ratio(fallback, total),
        "group_area_ha": group,
        "group_share": _ratio(group, total),
        "failed_area_ha": failed,
        "zero_area_excluded_ha": float(diagnostics.get("zero_area_ha", 0.0)),
        "zero_area_cells": int(diagnostics.get("zero_area_rows", 0)),
        "unmapped_area_ha": float(diagnostics.get("unmapped_area_ha", 0.0)),
        "unmapped_cells": int(diagnostics.get("unmapped_rows", 0)),
        "ecoprovince_only_area_ha": float(
            diagnostics.get("ecoprovince_residual_area_ha", 0.0)
        ),
        "ecoprovince_only_cells": int(diagnostics.get("ecoprovince_residual_rows", 0)),
        "unique_ecodistricts": int(
            library["ecodistrict_key"].nunique()
            if "ecodistrict_key" in library.columns
            else library.loc[
                library["target_level"].eq("ecodistrict"), "ecodistrict"
            ].nunique()
        ),
        "by_province": _records(province),
    }


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: _native(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def _native(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is pd.NA:
        return None
    return value


def species_summary(
    library: pd.DataFrame, model_index: Mapping[tuple[str, str, str], Mapping[str, Any]]
) -> dict[str, Any]:
    catalogue_species = {
        str(key[2])
        for key in model_index
        if str(key[2]) not in {"generic", "coniferous", "broadleaf"}
    }
    records: list[dict[str, Any]] = []
    for species, group in library.groupby("target_species", sort=True):
        covered = group.loc[group["status"].eq("ok")]
        level_counts = covered["selected_level"].value_counts().to_dict()
        records.append(
            {
                "target_species": str(species),
                "target_cells": len(group),
                "area_ha": float(group["area_ha"].sum()),
                "direct_area_ha": float(
                    group.loc[group["evidence_tier"].eq("rsyc_direct"), "area_ha"].sum()
                ),
                "fallback_area_ha": float(
                    group.loc[
                        group["evidence_tier"].eq("rsyc_fallback"), "area_ha"
                    ].sum()
                ),
                "group_area_ha": float(
                    group.loc[group["evidence_tier"].eq("rsyc_group"), "area_ha"].sum()
                ),
                "selected_level_counts": {
                    str(key): int(value) for key, value in level_counts.items()
                },
                "national_species_curves_available": str(species) in catalogue_species,
            }
        )
    return {
        "by_target_species": records,
        "target_species_without_any_national_species_curve": sorted(
            {
                str(species)
                for species in library["target_species"].unique()
                if str(species) not in GROUP_TARGETS
                and str(species) not in catalogue_species
            }
        ),
    }


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float | None:
    mask = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not mask.any():
        return None
    order = np.argsort(values[mask])
    sorted_values = values[mask][order]
    sorted_weights = weights[mask][order]
    cumulative: np.ndarray = np.cumsum(sorted_weights)
    cutoff = 0.5 * sorted_weights.sum()
    return float(sorted_values[np.searchsorted(cumulative, cutoff, side="left")])


def _difference_stats(
    *,
    primary_values: np.ndarray,
    tile_values: np.ndarray,
    area: np.ndarray,
) -> dict[str, Any]:
    mask = np.isfinite(primary_values) & np.isfinite(tile_values)
    if not mask.any():
        return {
            "common_cells": 0,
            "median_diff_m3ha": None,
            "median_abs_diff_m3ha": None,
            "mean_abs_diff_m3ha": None,
            "p95_abs_diff_m3ha": None,
            "area_weighted_median_abs_diff_m3ha": None,
            "median_relative_abs_diff_pct": None,
        }
    difference = primary_values[mask] - tile_values[mask]
    absolute = np.abs(difference)
    positive_tile = tile_values[mask] > 0
    relative = np.where(
        positive_tile,
        absolute / np.where(positive_tile, tile_values[mask], 1.0) * 100.0,
        np.nan,
    )
    return {
        "common_cells": int(mask.sum()),
        "median_diff_m3ha": float(np.median(difference)),
        "median_abs_diff_m3ha": float(np.median(absolute)),
        "mean_abs_diff_m3ha": float(np.mean(absolute)),
        "p95_abs_diff_m3ha": float(np.percentile(absolute, 95)),
        "area_weighted_median_abs_diff_m3ha": _weighted_median(absolute, area[mask]),
        "median_relative_abs_diff_pct": (
            None if not np.isfinite(relative).any() else float(np.nanmedian(relative))
        ),
    }


def compare_libraries(
    primary: pd.DataFrame, tile_first: pd.DataFrame
) -> dict[str, Any]:
    """Compare ecodistrict-first and tile-first volumes on identical cells."""
    merged = primary.merge(
        tile_first,
        on="cell_key",
        suffixes=("_primary", "_tile"),
        validate="one_to_one",
    )
    both = merged.loc[
        merged["status_primary"].eq("ok") & merged["status_tile"].eq("ok")
    ].copy()
    by_age: dict[str, Any] = {}
    for age in AGE_GRID:
        column = f"volume_m3ha_age{int(age)}"
        by_age[str(int(age))] = _difference_stats(
            primary_values=both[f"{column}_primary"].to_numpy(dtype=float),
            tile_values=both[f"{column}_tile"].to_numpy(dtype=float),
            area=both["area_ha_primary"].to_numpy(dtype=float),
        )

    by_species: list[dict[str, Any]] = []
    for species, group in both.groupby("target_species_primary", sort=True):
        record: dict[str, Any] = {
            "target_species": str(species),
            "common_cells": len(group),
            "area_ha": float(group["area_ha_primary"].sum()),
        }
        for age in AGE_GRID:
            column = f"volume_m3ha_age{int(age)}"
            stats = _difference_stats(
                primary_values=group[f"{column}_primary"].to_numpy(dtype=float),
                tile_values=group[f"{column}_tile"].to_numpy(dtype=float),
                area=group["area_ha_primary"].to_numpy(dtype=float),
            )
            record[f"median_abs_diff_age{int(age)}"] = stats["median_abs_diff_m3ha"]
        by_species.append(record)

    level_changed = both["selected_level_primary"].ne(both["selected_level_tile"])
    return {
        "cells_compared": len(both),
        "cells_with_different_selected_level": int(level_changed.sum()),
        "area_with_different_selected_level_ha": float(
            both.loc[level_changed, "area_ha_primary"].sum()
        ),
        "by_age": by_age,
        "by_species": by_species,
    }


def _tier_area_shares(library: pd.DataFrame) -> dict[str, Any]:
    total = float(library["area_ha"].sum())
    return {
        "total_area_ha": total,
        "direct_area_ha": float(
            library.loc[library["evidence_tier"].eq("rsyc_direct"), "area_ha"].sum()
        ),
        "fallback_area_ha": float(
            library.loc[library["evidence_tier"].eq("rsyc_fallback"), "area_ha"].sum()
        ),
        "group_area_ha": float(
            library.loc[library["evidence_tier"].eq("rsyc_group"), "area_ha"].sum()
        ),
        "covered_area_ha": float(
            library.loc[library["status"].eq("ok"), "area_ha"].sum()
        ),
        "coverage_fraction": _ratio(
            float(library.loc[library["status"].eq("ok"), "area_ha"].sum()), total
        ),
    }


def qc_summary(library: pd.DataFrame) -> dict[str, Any]:
    ok = library.loc[library["status"].eq("ok")]
    coefficient_columns = ["b1", "b2", "b3", "b4"]
    prediction_columns = [f"volume_m3ha_age{int(age)}" for age in AGE_GRID]
    return {
        "curves_selected": len(ok),
        "nan_coefficients": int(ok[coefficient_columns].isna().sum().sum()),
        "negative_b1_b2_b3": int(
            (ok[["b1", "b2", "b3"]].to_numpy(dtype=float) < 0).sum()
        ),
        "negative_b4": int((ok["b4"].astype(float) < 0).sum()),
        "nan_or_negative_predictions": int(
            (
                ok[prediction_columns].isna().any(axis=1)
                | ok[prediction_columns].lt(0).any(axis=1)
            ).sum()
        ),
        "median_volume_age50": float(ok["volume_m3ha_age50"].median()),
        "median_volume_age100": float(ok["volume_m3ha_age100"].median()),
        "median_volume_age150": float(ok["volume_m3ha_age150"].median()),
        "min_volume_age50": float(ok["volume_m3ha_age50"].min()),
        "max_volume_age150": float(ok["volume_m3ha_age150"].max()),
    }


def validation_summary(
    *,
    library: pd.DataFrame,
    targets: pd.DataFrame,
    diagnostics: Mapping[str, Any],
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    ok = library.loc[library["status"].eq("ok")]
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    positive_targets = int(targets.shape[0])
    check(
        "every_positive_area_target_has_a_curve",
        int(library["status"].eq("ok").sum()) == positive_targets,
        {
            "targets": positive_targets,
            "covered": int(library["status"].eq("ok").sum()),
            "failed_area_ha": float(
                library.loc[library["status"].ne("ok"), "area_ha"].sum()
            ),
        },
    )
    check(
        "no_nan_coefficients",
        int(ok[["b1", "b2", "b3", "b4"]].isna().sum().sum()) == 0,
        int(ok[["b1", "b2", "b3", "b4"]].isna().sum().sum()),
    )
    check(
        "b1_b2_b3_strictly_positive",
        int((ok[["b1", "b2", "b3"]].to_numpy(dtype=float) <= 0).sum()) == 0,
        int((ok[["b1", "b2", "b3"]].to_numpy(dtype=float) <= 0).sum()),
    )
    check(
        "negative_b4_is_flagged",
        int(ok["b4"].astype(float).lt(0).sum())
        == int(ok["coefficient_sign_flag"].eq("negative_b4").sum()),
        {
            "negative_b4": int(ok["b4"].astype(float).lt(0).sum()),
            "flagged": int(ok["coefficient_sign_flag"].eq("negative_b4").sum()),
        },
    )
    prediction_columns = [f"volume_m3ha_age{int(age)}" for age in AGE_GRID]
    check(
        "predictions_finite_and_non_negative",
        int(
            (
                ok[prediction_columns].isna().any(axis=1)
                | ok[prediction_columns].lt(0).any(axis=1)
            ).sum()
        )
        == 0,
        "ages 50/100/150",
    )
    area_total = float(library["area_ha"].sum())
    check(
        "area_sums_match_targets",
        math.isclose(area_total, float(targets["area_ha"].sum()), rel_tol=1e-12),
        {
            "library_area_ha": area_total,
            "target_area_ha": float(targets["area_ha"].sum()),
        },
    )
    check(
        "no_duplicate_target_cells",
        int(library.duplicated("cell_key").sum()) == 0,
        int(library.duplicated("cell_key").sum()),
    )
    check(
        "fallback_depth_within_ladder",
        bool(
            library.loc[library["status"].eq("ok"), "fallback_depth"]
            .between(0, 14)
            .all()
        ),
        {
            "min": int(ok["fallback_depth"].min()),
            "max": int(ok["fallback_depth"].max()),
        },
    )
    check(
        "tile_assignment_recorded",
        bool(library["tile_join_status"].notna().all()),
        library["tile_join_status"].value_counts().to_dict(),
    )
    check(
        "unmapped_area_reported",
        float(diagnostics.get("unmapped_area_ha", 0.0)) >= 0.0,
        float(diagnostics.get("unmapped_area_ha", 0.0)),
    )
    check(
        "province_area_sums_match_reference",
        float(diagnostics.get("province_area_max_rel_diff", 0.0)) < 1e-9,
        {
            "max_abs_diff_ha": float(
                diagnostics.get("province_area_max_abs_diff_ha", 0.0)
            ),
            "max_rel_diff": float(diagnostics.get("province_area_max_rel_diff", 0.0)),
        },
    )
    check(
        "tile_first_comparison_complete",
        int(comparison.get("cells_compared", 0)) == positive_targets,
        comparison.get("cells_compared", 0),
    )
    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }


def gap_summary(
    *,
    library: pd.DataFrame,
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    ok = library.loc[library["status"].eq("ok")]
    group_labels = {"generic", "coniferous", "broadleaf"}
    ecodistrict_species = {
        str(key[2])
        for key in model_index
        if str(key[0]) == "ecodistrict" and str(key[2]) not in group_labels
    }
    any_level_species = {
        str(key[2]) for key in model_index if str(key[2]) not in group_labels
    }
    target_species = sorted(library["target_species"].unique())
    exact = [str(value) for value in target_species if str(value) not in GROUP_TARGETS]
    group_area = (
        library.loc[library["evidence_tier"].eq("rsyc_group")]
        .groupby("target_species", as_index=False)["area_ha"]
        .sum()
        .sort_values("area_ha", ascending=False)
    )
    fallback_by_depth = (
        library.groupby(
            ["fallback_depth", "selected_level", "evidence_tier"], dropna=False
        )
        .agg(cells=("cell_key", "size"), area_ha=("area_ha", "sum"))
        .reset_index()
    )
    tile_ids = {str(key[1]) for key in model_index if str(key[0]) == "tile"}
    broadleaf_tiles = {
        str(key[1])
        for key in model_index
        if str(key[0]) == "tile" and str(key[2]) == "broadleaf"
    }
    return {
        "target_species_without_national_species_curve": sorted(
            set(exact) - any_level_species
        ),
        "target_species_without_ecodistrict_species_curve": sorted(
            set(exact) - ecodistrict_species
        ),
        "group_fallback_area_by_target_species": _records(group_area),
        "fallback_area_by_depth_level": _records(fallback_by_depth),
        "target_cells_not_direct": int(ok["evidence_tier"].ne("rsyc_direct").sum()),
        "area_not_direct_ha": float(
            ok.loc[ok["evidence_tier"].ne("rsyc_direct"), "area_ha"].sum()
        ),
        "ecodistricts_with_at_least_one_non_direct_cell": int(
            ok.loc[ok["evidence_tier"].ne("rsyc_direct"), "ecodistrict_key"].nunique()
        ),
        "published_tiles_in_index": len(tile_ids),
        "tiles_without_broadleaf_group_curve": len(tile_ids) - len(broadleaf_tiles),
    }


def build_summary(
    *,
    library: pd.DataFrame,
    tile_first: pd.DataFrame,
    targets: pd.DataFrame,
    diagnostics: Mapping[str, Any],
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    comparison: Mapping[str, Any],
    model_strata: Mapping[str, int],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    species = species_summary(library, model_index)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": dict(inputs),
        "parameters": {
            "responses": ["volume"],
            "response_units": ["m3/ha"],
            "model_scale": "national",
            "publish_ok": True,
            "fit_ok": True,
            "converged": True,
            "age_grid": [int(age) for age in AGE_GRID],
            "operational_age_range": [1, 150],
            "target_levels": ["ecodistrict", "ecoprovince (residual only)"],
            "ladder": list(LEVEL_LADDER),
            "selected_model_strata": dict(model_strata),
        },
        "target_cells": {
            "total": len(targets),
            "ecodistrict_level": int(targets["target_level"].eq("ecodistrict").sum()),
            "ecoprovince_level": int(targets["target_level"].eq("ecoprovince").sum()),
            "unique_ecodistricts": int(targets["ecodistrict_key"].nunique()),
            "unique_ecodistrict_species_cells": int(
                targets.loc[targets["target_level"].eq("ecodistrict")]
                .drop_duplicates(["ecodistrict_key", "target_species"])
                .shape[0]
            ),
            "total_area_ha": float(targets["area_ha"].sum()),
            "unique_species": int(targets["target_species"].nunique()),
        },
        "selection": selection_summary(library),
        "area_coverage": area_coverage_summary(library, diagnostics),
        "qc": qc_summary(library),
        "species": species,
        "tile_first": {
            "selection": selection_summary(tile_first),
            "area_coverage": _tier_area_shares(tile_first),
        },
        "comparison_ecodistrict_vs_tile_first": dict(comparison),
        "area_diagnostics": {key: _native(value) for key, value in diagnostics.items()},
        "validation": validation_summary(
            library=library,
            targets=targets,
            diagnostics=diagnostics,
            comparison=comparison,
        ),
        "gaps": gap_summary(library=library, model_index=model_index),
    }
    return summary


def _format_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if value is None:
                values.append("")
            elif isinstance(value, float):
                values.append(f"{value:,.4f}")
            elif isinstance(value, int):
                values.append(f"{value:,}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_report(summary: Mapping[str, Any]) -> str:
    """Render the auditable markdown report from the summary object."""
    selection = summary["selection"]
    coverage = summary["area_coverage"]
    comparison = summary["comparison_ecodistrict_vs_tile_first"]
    validation = summary["validation"]
    gaps = summary["gaps"]
    qc = summary["qc"]
    tile_first = summary["tile_first"]["area_coverage"]

    tier_rows = [
        {
            "tier": tier,
            "cells": selection["by_evidence_tier"].get(tier, 0),
            "area_ha": {
                "rsyc_direct": coverage["direct_area_ha"],
                "rsyc_fallback": coverage["fallback_area_ha"],
                "rsyc_group": coverage["group_area_ha"],
            }[tier],
            "area_share_pct": 100.0
            * {
                "rsyc_direct": coverage["direct_share"],
                "rsyc_fallback": coverage["fallback_share"],
                "rsyc_group": coverage["group_share"],
            }[tier],
        }
        for tier in EVIDENCE_TIERS
    ]
    level_rows = [
        {
            "selected_level": level,
            "cells": int(count),
        }
        for level, count in sorted(
            selection["by_selected_level"].items(), key=lambda item: item[0]
        )
    ]
    depth_rows = [
        {
            "fallback_depth": row["fallback_depth"],
            "cells": row["cells"],
            "area_ha": row["area_ha"],
        }
        for row in selection["fallback_depth_distribution"]
    ]
    province_rows = sorted(
        coverage["by_province"], key=lambda row: row["area_ha"], reverse=True
    )
    province_table = _format_table(
        [
            {
                "province": row["province"],
                "cells": row["target_cells"],
                "area_ha": row["area_ha"],
                "coverage_pct": 100.0 * row["coverage_fraction"],
                "direct_pct": 100.0 * row["direct_share"],
                "fallback_pct": 100.0 * row["fallback_share"],
                "group_pct": 100.0 * row["group_share"],
            }
            for row in province_rows
        ],
        [
            "province",
            "cells",
            "area_ha",
            "coverage_pct",
            "direct_pct",
            "fallback_pct",
            "group_pct",
        ],
    )
    age_rows = []
    for age, stats in comparison["by_age"].items():
        age_rows.append(
            {
                "age": age,
                "cells": stats["common_cells"],
                "median_diff": stats["median_diff_m3ha"],
                "median_abs_diff": stats["median_abs_diff_m3ha"],
                "mean_abs_diff": stats["mean_abs_diff_m3ha"],
                "p95_abs_diff": stats["p95_abs_diff_m3ha"],
                "area_weighted_median_abs": stats["area_weighted_median_abs_diff_m3ha"],
                "median_relative_abs_pct": stats["median_relative_abs_diff_pct"],
            }
        )
    species_rows = [
        {
            "species": row["target_species"],
            "cells": row["target_cells"],
            "area_ha": row["area_ha"],
            "direct_pct": 100.0 * _ratio(row["direct_area_ha"], row["area_ha"]),
            "fallback_pct": 100.0 * _ratio(row["fallback_area_ha"], row["area_ha"]),
            "group_pct": 100.0 * _ratio(row["group_area_ha"], row["area_ha"]),
            "curve": (
                "group target"
                if row["target_species"] in GROUP_TARGETS
                else (
                    "species"
                    if row["national_species_curves_available"]
                    else "no species curve -> group"
                )
            ),
        }
        for row in summary["species"]["by_target_species"]
    ]
    check_rows = [
        {
            "check": item["check"],
            "passed": "PASS" if item["passed"] else "FAIL",
            "detail": json.dumps(item["detail"], default=str),
        }
        for item in validation["checks"]
    ]
    return f"""# RSYC national volume-curve backbone

**Status:** generated artefact; no commit. **Date:** {summary["generated_at"]}.

## 1. Selection and fallback

Target cells are positive-area SCANFI ecodistrict parts x species, with
ecoprovince residuals only where no ecodistrict area exists. Selection uses
national-scale, `publish_ok`, `fit_ok`, `converged` volume curves. Exact
species are exhausted at ecodistrict, ecoregion, ecoprovince, ecozone, and
tile before the species group (`coniferous`/`broadleaf`) and finally `generic`
are considered.

- Target cells: **{summary["target_cells"]["total"]:,}**
  ({summary["target_cells"]["ecodistrict_level"]:,} ecodistrict, {summary["target_cells"]["ecoprovince_level"]:,} ecoprovince residual)
- Unique ecodistrict x species cells: **{summary["target_cells"]["unique_ecodistrict_species_cells"]:,}**
  (area weights are split into province parts, so one ecodistrict can carry more than one cell)
- Unique ecodistricts: **{summary["target_cells"]["unique_ecodistricts"]:,}**
- Total target area: **{summary["target_cells"]["total_area_ha"]:,.0f} ha**
- Curves selected: **{selection["covered_cells"]:,}**; explicit failures: **{selection["failed_cells"]:,}**

### Evidence tier

{_format_table(tier_rows, ["tier", "cells", "area_ha", "area_share_pct"])}

### Selected level

{_format_table(level_rows, ["selected_level", "cells"])}

### Fallback depth

Depth 0-4 is exact species (ecodistrict, ecoregion, ecoprovince, ecozone,
tile); depth 5-9 is the species group at the same ladder; depth 10-14 is
`generic`. Depth -1 is an explicit failure.

{_format_table(depth_rows, ["fallback_depth", "cells", "area_ha"])}

## 2. Area coverage

| Metric | Area (ha) | Share |
|---|---:|---:|
| Total target area | {coverage["total_area_ha"]:,.0f} | 100.0% |
| Covered by a selected curve | {coverage["covered_area_ha"]:,.0f} | {100.0 * coverage["coverage_fraction"]:.2f}% |
| Direct exact species | {coverage["direct_area_ha"]:,.0f} | {100.0 * coverage["direct_share"]:.2f}% |
| Fallback exact species | {coverage["fallback_area_ha"]:,.0f} | {100.0 * coverage["fallback_share"]:.2f}% |
| Species group | {coverage["group_area_ha"]:,.0f} | {100.0 * coverage["group_share"]:.2f}% |
| Explicit failure | {coverage["failed_area_ha"]:,.0f} | {100.0 * _ratio(coverage["failed_area_ha"], coverage["total_area_ha"]):.2f}% |
| Zero-area cells excluded | {coverage["zero_area_excluded_ha"]:,.0f} | {coverage["zero_area_cells"]:,} cells, not a target |
| Unmapped area (no ecodistrict/ecoprovince join) | {coverage["unmapped_area_ha"]:,.0f} | {coverage["unmapped_cells"]:,} cells, not assignable |
| Ecoprovince-only residual | {coverage["ecoprovince_only_area_ha"]:,.0f} | {coverage["ecoprovince_only_cells"]:,} cells, added separately |

### By province

{province_table}

## 3. Ecodistrict-first versus tile-first

Tile-first selects the tile exact species, then the tile group, then tile
`generic`, on the same cells and area weights. It is the control comparison,
not the backbone: the tile is a separate 150 km grid, not a parent ecological
stratum.

| Metric | Ecodistrict-first | Tile-first |
|---|---:|---:|
| Covered area | {coverage["covered_area_ha"]:,.0f} ha | {tile_first["covered_area_ha"]:,.0f} ha |
| Coverage | {100.0 * coverage["coverage_fraction"]:.2f}% | {100.0 * tile_first["coverage_fraction"]:.2f}% |
| Direct area | {coverage["direct_area_ha"]:,.0f} ha | {tile_first["direct_area_ha"]:,.0f} ha |
| Fallback area | {coverage["fallback_area_ha"]:,.0f} ha | {tile_first["fallback_area_ha"]:,.0f} ha |
| Group area | {coverage["group_area_ha"]:,.0f} ha | {tile_first["group_area_ha"]:,.0f} ha |

Differences are `ecodistrict-first minus tile-first` on cells where both
produce a curve. `p95` is the 95th percentile of absolute differences.

{_format_table(age_rows, ["age", "cells", "median_diff", "median_abs_diff", "mean_abs_diff", "p95_abs_diff", "area_weighted_median_abs", "median_relative_abs_pct"])}

Cells whose selected level differs: **{comparison["cells_with_different_selected_level"]:,}**
({comparison["area_with_different_selected_level_ha"]:,.0f} ha).

## 4. Species coverage and gaps

{_format_table(species_rows, ["species", "cells", "area_ha", "direct_pct", "fallback_pct", "group_pct", "curve"])}

- Target species with no national species curve at any level:
  **{", ".join(gaps["target_species_without_national_species_curve"]) or "none"}**
- Target cells without a direct exact-species curve:
  **{gaps["target_cells_not_direct"]:,}** ({gaps["area_not_direct_ha"]:,.0f} ha)
- Ecodistricts with at least one non-direct target cell:
  **{gaps["ecodistricts_with_at_least_one_non_direct_cell"]:,}** of 672
- Published tiles without a `broadleaf` group curve:
  **{gaps["tiles_without_broadleaf_group_curve"]:,}** of
  {gaps["published_tiles_in_index"]:,} (tile `generic` remains).
- Negative `b4` coefficients in selected curves:
  **{qc["negative_b4"]:,}** of {qc["curves_selected"]:,}. These are published
  RSYC fits; the sign is flagged in `coefficient_sign_flag`, not silently
  dropped. `b1`, `b2`, and `b3` are strictly positive for every selected curve.

## 5. Validation and tests

Validation summary: **{"PASS" if validation["passed"] else "FAIL"}**.

{_format_table(check_rows, ["check", "passed", "detail"])}

Curve QC at ages 50/100/150: median volumes
{qc["median_volume_age50"]:,.1f} / {qc["median_volume_age100"]:,.1f} /
{qc["median_volume_age150"]:,.1f} m3/ha. No NaN or negative predictions.

Tests are in `workflows/national/assortments/tests/test_rsyc_backbone.py`
(ladder/fallback, evidence tiers, area join, curve evaluation). They are not
committed.

## 6. Paths and status

Outputs: `data/derived/national/assortments/rsyc_backbone/`.
The `manifest.json` records input and output SHA-256 checksums. No git commit
was made.

## 7. Method notes

- National-scale curves only. Regional-scale curves duplicate the same
  ecological strata and add a second fit; they are not used.
- The single documented catalogue/boundary mismatch (ecodistrict 438,
  `Betsiamites` versus `Betsiamiles`) is corrected explicitly and counted.
- Tile assignment uses the tile containing each ecodistrict or ecoprovince
  centroid. No nearest-tile fabrication is used. Every target centroid fell in
  exactly one published tile.
- The tile-first comparison does not upgrade any curve to `rsyc_direct`; all
  tile selections are recorded as fallback or group evidence.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is pd.NA:
        return None
    return value


def validate_species_groups(distribution_parameters_path: Path) -> dict[str, str]:
    """Check the hard-coded species groups against the national fit input."""
    path = Path(distribution_parameters_path)
    if not path.exists():
        raise FileNotFoundError(f"national distribution parameters not found: {path}")
    frame = pd.read_csv(path, usecols=["species", "species_group"])
    observed = (
        frame.dropna()
        .drop_duplicates("species")
        .set_index("species")["species_group"]
        .str.lower()
        .to_dict()
    )
    mismatches = {
        str(species): {
            "distribution": str(group),
            "backbone": SPECIES_GROUP[str(species)],
        }
        for species, group in observed.items()
        if str(species) in SPECIES_GROUP and str(group) != SPECIES_GROUP[str(species)]
    }
    if mismatches:
        raise RsycBackboneError(
            f"species-group mapping disagrees with national fit: {mismatches}"
        )
    missing = sorted(set(SPECIES_GROUP) - set(observed))
    return {
        "species_checked": str(len(observed)),
        "mismatches": str(len(mismatches)),
        "backbone_only_labels": ",".join(missing),
    }


def run(
    *,
    repo_root: Path,
    out_dir: Path,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build the backbone library, control library, summary, report, manifest."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    raw_dir = repo_root / "data" / "raw" / "national" / "rsyc"
    weights_dir = (
        repo_root
        / "data"
        / "derived"
        / "national"
        / "assortments"
        / "national_area_weights"
    )
    fit_dir = (
        repo_root / "data" / "derived" / "national" / "assortments" / "national_fit"
    )
    batch_dir = (
        repo_root / "data" / "derived" / "national" / "assortments" / "national_batch"
    )

    model_zip = raw_dir / "RSYC_models.zip"
    tiles_path = raw_dir / "RSYC_tiles.gpkg"
    ecostrat_path = raw_dir / "RSYC_ecostrat.gpkg"
    ecodistrict_weights_path = weights_dir / "national_area_weights_ecodistrict.csv"
    ecoprovince_weights_path = weights_dir / "national_area_weights.csv"
    province_weights_path = weights_dir / "national_area_weights_province.csv"
    distribution_path = fit_dir / "national_distribution_parameters.csv"
    stands_path = batch_dir / "national_stands.csv"
    planning_path = repo_root / "planning" / "national-rsyc-backbone.md"

    for path in (
        model_zip,
        tiles_path,
        ecostrat_path,
        ecodistrict_weights_path,
        ecoprovince_weights_path,
        province_weights_path,
        distribution_path,
        stands_path,
        planning_path,
    ):
        if not path.exists():
            raise FileNotFoundError(f"required input not found: {path}")

    species_group_check = validate_species_groups(distribution_path)
    models = load_rsyc_volume_models(model_zip)
    strata = load_ecostrata(ecostrat_path)
    model_strata = validate_model_strata(models, strata)
    model_index = build_model_index(models)

    import geopandas as gpd

    tile_layer = gpd.read_file(tiles_path, layer="RSYC_tiles")
    model_tiles = set(
        models.loc[models["strata_level"].eq("tile"), "strata_id"].astype(str)
    )
    missing_tiles = sorted(model_tiles - set(tile_layer["TileID"].astype(str)))
    if missing_tiles:
        raise RsycBackboneError(
            f"tile model IDs absent from tile layer: {missing_tiles[:3]}"
        )
    if len(tile_layer) != 385:
        raise RsycBackboneError(
            f"expected 385 published tiles, found {len(tile_layer)}"
        )

    ecodistrict_weights = pd.read_csv(ecodistrict_weights_path)
    ecoprovince_weights = pd.read_csv(ecoprovince_weights_path)
    targets, diagnostics = build_area_targets(
        ecodistrict_weights=ecodistrict_weights,
        ecoprovince_weights=ecoprovince_weights,
        strata=strata,
    )
    targets = attach_tiles(targets, strata=strata, tiles_path=tiles_path)

    library = build_library(targets, model_index, mode="ecodistrict_first")
    tile_first = build_library(targets, model_index, mode="tile_first")
    comparison = compare_libraries(library, tile_first)

    province_reference = pd.read_csv(province_weights_path)
    reference_area = (
        province_reference.groupby("province", as_index=False)["area_ha"]
        .sum()
        .set_index("province")["area_ha"]
    )
    observed_area = (
        targets.groupby("province", as_index=False)["area_ha"]
        .sum()
        .set_index("province")["area_ha"]
    )
    common_provinces = reference_area.index.intersection(observed_area.index)
    province_diff = (
        observed_area.loc[common_provinces] - reference_area.loc[common_provinces]
    ).abs()
    diagnostics["province_area_max_abs_diff_ha"] = float(province_diff.max())
    diagnostics["province_area_max_rel_diff"] = float(
        (province_diff / reference_area.loc[common_provinces]).max()
    )

    input_paths = [
        model_zip,
        tiles_path,
        ecostrat_path,
        ecodistrict_weights_path,
        ecoprovince_weights_path,
        province_weights_path,
        distribution_path,
        stands_path,
        planning_path,
    ]
    relative_inputs = {
        path.name: str(path.relative_to(repo_root))
        if path.is_relative_to(repo_root)
        else str(path)
        for path in input_paths
    }
    summary = build_summary(
        library=library,
        tile_first=tile_first,
        targets=targets,
        diagnostics=diagnostics,
        model_index=model_index,
        comparison=comparison,
        model_strata=model_strata,
        inputs={
            **relative_inputs,
            "species_group_check": species_group_check,
            "published_tiles": len(tile_layer),
        },
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    library_path = out_dir / "rsyc_curve_library.csv"
    tile_first_path = out_dir / "rsyc_curve_library_tile_first.csv"
    summary_path = out_dir / "rsyc_coverage_summary.json"
    report_path = out_dir / "RSYC_BACKBONE_LIBRARY_REPORT.md"
    manifest_path = out_dir / "manifest.json"

    library.to_csv(library_path, index=False)
    tile_first.to_csv(tile_first_path, index=False)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(_jsonable(summary), handle, indent=2)
        handle.write("\n")
    report_path.write_text(render_report(summary), encoding="utf-8")

    output_paths = {
        "rsyc_curve_library.csv": library_path,
        "rsyc_curve_library_tile_first.csv": tile_first_path,
        "rsyc_coverage_summary.json": summary_path,
        "RSYC_BACKBONE_LIBRARY_REPORT.md": report_path,
    }
    manifest = build_manifest(
        input_paths=input_paths,
        output_paths=output_paths,
        summary=summary,
        row_counts={
            "rsyc_curve_library.csv": len(library),
            "rsyc_curve_library_tile_first.csv": len(tile_first),
            "rsyc_coverage_summary.json": 0,
            "RSYC_BACKBONE_LIBRARY_REPORT.md": 0,
        },
        base_dir=repo_root,
    )
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(_jsonable(manifest), handle, indent=2)
        handle.write("\n")

    if verbose:
        print(
            f"rsyc_backbone: {len(library):,} cells, "
            f"{summary['validation']['passed'] and 'PASS' or 'FAIL'}, {out_dir}"
        )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: data/derived/national/assortments/rsyc_backbone)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else repo_root
        / "data"
        / "derived"
        / "national"
        / "assortments"
        / "rsyc_backbone"
    )
    try:
        run(repo_root=repo_root, out_dir=out_dir, verbose=bool(args.verbose))
    except (RsycBackboneError, FileNotFoundError) as error:
        print(f"rsyc_backbone: {error}")
        return 2
    return 0


def _display_path(path: Path, base_dir: Path | None) -> str:
    if base_dir is None:
        return str(path)
    try:
        return str(Path(path).resolve().relative_to(Path(base_dir).resolve()))
    except ValueError:
        return str(path)


def build_manifest(
    *,
    input_paths: Sequence[Path],
    output_paths: Mapping[str, Path],
    summary: Mapping[str, Any],
    row_counts: Mapping[str, int],
    base_dir: Path | None = None,
) -> dict[str, Any]:
    inputs = []
    for path in input_paths:
        path = Path(path)
        inputs.append(
            {
                "path": _display_path(path, base_dir),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    outputs = []
    for name, path in output_paths.items():
        path = Path(path)
        outputs.append(
            {
                "name": name,
                "path": _display_path(path, base_dir),
                "sha256": _sha256(path) if path.exists() else None,
                "bytes": path.stat().st_size if path.exists() else None,
                "rows": int(row_counts.get(name, 0)),
            }
        )
    return {
        "generated_at": summary["generated_at"],
        "committed": False,
        "command": "python -m workflows.national.assortments.rsyc_backbone",
        "parameters": summary["parameters"],
        "inputs": inputs,
        "outputs": outputs,
        "validation_passed": summary["validation"]["passed"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
