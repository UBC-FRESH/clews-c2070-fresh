"""Derive E2 v2 distribution inputs from open provincial forest inventories.

The audit artifact (``provincial_open_inventory_audit_v1``) records which
attributes each open layer publishes.  This module turns the *attributes that
actually exist* into small, reproducible distribution tables:

* AB AVI Crown: species composition, height, stand origin (full FGDB).
* SK FRI Forest Stands: height, basal area, QMD, stems, gross/merchantable
  volume and softwood/hardwood class (checked-in 500-stand sample).
* MB FRI 1970s: species composition and stand age (full FGDB).
* NS Forest Inventory: species composition, height, average diameter, basal
  area and merchantable volume (full shapefile DBF).
* PE CLUI 2010: species composition and height; PE EFI 2020: height, inferred
  QMD from basal area and merchantable stems, volume (checked-in samples).
* ON / NL / NT / NU: no open stand attributes suitable for a distribution; the
  artifact records that gap and E2 v2 falls back to SCANFI composition and
  measured donor geometry.

No request, DUA, restricted or official OSeCAN input is read.  Every table is
area-weighted where the source publishes a polygon area or hectare field.
"""

# The report and markdown literals are long; adjacent literals are intentional.
# ruff: noqa: ISC004

from __future__ import annotations

import argparse
import hashlib
import math
import re
import struct
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["ARTIFACT_ID", "DEFAULT_OUTPUT_DIR", "PROVINCES", "build"]

ARTIFACT_ID = "provincial_open_inventory_distributions_v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "inventory" / ARTIFACT_ID
)
AUDIT_DIR = (
    REPO_ROOT / "data/derived/national/inventory/provincial_open_inventory_audit_v1"
)
SPECIES_MAPPING_PATH = (
    REPO_ROOT / "data/raw/casfri_v5/translation_tables/species_code_mapping.csv"
)
RETRIEVAL_DATE = "2026-11-23"

PROVINCES: tuple[str, ...] = ("AB", "SK", "MB", "NS", "PE", "ON", "NL", "NT", "NU")
QUANTILE_GRID: tuple[float, ...] = tuple(round(0.05 + 0.05 * step, 2) for step in range(19))
MIN_SPECIES_OBSERVATIONS = 20


class DistributionError(RuntimeError):
    """Raised when an inventory distribution contract is violated."""


# ---------------------------------------------------------------------------
# Small, explicit parsing boundaries
# ---------------------------------------------------------------------------

_CONIFER_GENERA = frozenset(
    {"ABIE", "PICE", "PINU", "LARI", "PSEU", "THUJ", "TSUG", "JUNI", "CUPRE", "TAXU"}
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _finite(value: Any, label: str) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _positive(value: Any, label: str) -> float | None:
    parsed = _finite(value, label)
    if parsed is None or parsed <= 0.0:
        return None
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> int:
    frame = pd.DataFrame(list(rows), columns=list(columns))
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return len(frame)


def _weighted_quantile(
    values: Sequence[float],
    weights: Sequence[float],
    quantile: float,
) -> float:
    if len(values) != len(weights):
        raise DistributionError("weighted quantile length mismatch")
    if not values:
        raise DistributionError("weighted quantile received no values")
    ordered = sorted(zip(values, weights, strict=True))
    total = math.fsum(weight for _, weight in ordered)
    if total <= 0.0:
        raise DistributionError("weighted quantile received non-positive total weight")
    target = quantile * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _weighted_rows_at(
    rows: Sequence[tuple[float, float]],
    positions: Sequence[float],
) -> list[int]:
    """Return row indexes at weighted quantile positions.

    ``rows`` is ``(sort_key, weight)``.  The returned index points at the first
    row whose cumulative weight reaches the requested position.  Ties are
    resolved deterministically by the caller's stable sort.
    """

    if not rows:
        raise DistributionError("weighted row draw received no rows")
    total = math.fsum(weight for _, weight in rows)
    if total <= 0.0:
        raise DistributionError("weighted row draw received non-positive weight")
    ordered = sorted(range(len(rows)), key=lambda index: (rows[index][0], index))
    cumulative = 0.0
    cumulative_at = [0.0] * len(ordered)
    for out_index, row_index in enumerate(ordered):
        cumulative += rows[row_index][1]
        cumulative_at[out_index] = cumulative
    selected: list[int] = []
    cursor = 0
    for position in positions:
        target = position * total
        while cursor < len(ordered) - 1 and cumulative_at[cursor] < target:
            cursor += 1
        selected.append(ordered[cursor])
    return selected


def _quantile_stats(
    values: Sequence[float],
    weights: Sequence[float] | None,
) -> dict[str, float | int]:
    if len(values) == 0:
        raise DistributionError("quantile stats received no values")
    values_array = np.asarray(values, dtype=float)
    if weights is None:
        weights_array = np.ones(len(values_array), dtype=float)
    else:
        weights_array = np.asarray(weights, dtype=float)
    if len(values_array) != len(weights_array):
        raise DistributionError("quantile stats length mismatch")
    if not np.isfinite(values_array).all() or not np.isfinite(weights_array).all():
        raise DistributionError("quantile stats received non-finite values")
    if (weights_array <= 0.0).any():
        raise DistributionError("quantile stats received non-positive weight")
    order = np.argsort(values_array, kind="mergesort")
    ordered_values = values_array[order]
    ordered_weights = weights_array[order]
    cumulative = np.cumsum(ordered_weights)
    total = float(cumulative[-1])
    if total <= 0.0:
        raise DistributionError("quantile stats received non-positive total weight")
    grid_positions = np.asarray(QUANTILE_GRID, dtype=float) * total
    indexes = np.searchsorted(cumulative, grid_positions, side="left")
    indexes = np.clip(indexes, 0, len(ordered_values) - 1)
    mean = float(np.average(ordered_values, weights=ordered_weights))
    variance = float(np.average((ordered_values - mean) ** 2, weights=ordered_weights))
    row: dict[str, float | int] = {
        "n": len(values_array),
        "min": round(float(ordered_values[0]), 6),
        "max": round(float(ordered_values[-1]), 6),
        "mean": round(mean, 6),
        "median": round(float(ordered_values[indexes[9]]), 6),
        "sd": round(math.sqrt(max(variance, 0.0)), 6),
    }
    for quantile, index in zip(QUANTILE_GRID, indexes, strict=True):
        row[f"p{round(quantile * 100):02d}"] = round(float(ordered_values[index]), 6)
    return row


# ---------------------------------------------------------------------------
# Provincial species code resolution
# ---------------------------------------------------------------------------

_CASFRI_TO_NFI: dict[str, str] = {
    "ABIE_BAL": "ABIE.BAL",
    "ABIE_LAS": "ABIE.LAS",
    "ABIE_AMA": "ABIE.AMA",
    "ABIE_GRA": "ABIE.GRA",
    "PICE_MAR": "PICE.MAR",
    "PICE_GLA": "PICE.GLA",
    "PICE_RUB": "PICE.RUB",
    "PICE_ENG": "PICE.ENG",
    "PICE_SIT": "PICE.SIT",
    "PICE_ABI": "PICE.ABI",
    "PICE_SPP": "PICE.SPP",
    "PINU_BAN": "PINU.BAN",
    "PINU_CON": "PINU.CON",
    "PINU_STR": "PINU.STR",
    "PINU_RES": "PINU.RES",
    "PINU_SYL": "PINU.SYL",
    "PINU_SPP": "PINU.SPP",
    "LARI_LAR": "LARI.LAR",
    "LARI_OCC": "LARI.OCC",
    "PSEU_MEN": "PSEU.MEN",
    "THUJ_OCC": "THUJ.OCC",
    "THUJ_PLI": "THUJ.PLI",
    "TSUG_HET": "TSUG.HET",
    "TSUG_CAN": "TSUG.CAN",
    "POPU_TRE": "POPU.TRE",
    "POPU_BAL": "POPU.BAL",
    "BETU_PAP": "BETU.PAP",
    "BETU_ALL": "BETU.ALL",
}


def _casfri_to_nfi(casfri_code: str) -> str | None:
    text = _text(casfri_code).upper()
    if not text:
        return None
    parts = text.split("_")
    if len(parts) < 2:
        return None
    key = f"{parts[0]}_{parts[1]}"
    return _CASFRI_TO_NFI.get(key)


def _species_group(nfi_species: str) -> str:
    genus = nfi_species.split(".", 1)[0]
    return "conifer" if genus in _CONIFER_GENERA else "deciduous"


def _load_species_lookup() -> dict[str, dict[str, tuple[str, str, str]]]:
    if not SPECIES_MAPPING_PATH.is_file():
        raise DistributionError(f"CASFRI species mapping not found: {SPECIES_MAPPING_PATH}")
    frame = pd.read_csv(SPECIES_MAPPING_PATH, dtype=str, keep_default_na=False)
    province_columns = {
        "AB": "ab_species_codes",
        "SK": "sk_species_codes",
        "MB": "mb_species_codes",
        "NS": "ns_species_codes",
        "PE": "pe_species_codes",
        "ON": "on_species_codes",
        "NL": "nl_species_codes",
        "NT": "nt_species_codes",
    }
    lookup: dict[str, dict[str, tuple[str, str, str]]] = {p: {} for p in PROVINCES}
    for row in frame.to_dict(orient="records"):
        casfri = _text(row.get("casfri_species_codes"))
        nfi = _casfri_to_nfi(casfri)
        if nfi is None:
            continue

        group = _species_group(nfi)
        for province, column in province_columns.items():
            code = _text(row.get(column)).upper()
            if not code:
                continue
            existing = lookup[province].get(code)
            if existing is None or existing[0] == "BROADLEAF":
                lookup[province][code] = (nfi, group, casfri)
    return lookup


def _resolve_code(
    province: str,
    raw_code: str,
    lookup: Mapping[str, Mapping[str, tuple[str, str, str]]],
) -> tuple[str, str, str, str] | None:
    code = _text(raw_code).upper()
    if not code:
        return None
    match = lookup[province].get(code)
    if match is not None:
        nfi, group, casfri = match
        return code, nfi, group, casfri
    # Provincial aggregate codes that do not carry a species translation.
    if code in {"H", "OH", "UH", "HS", "IH", "TH", "UC", "UNK", "UNKNOWN"}:
        return code, "BROADLEAF", "deciduous", "aggregate_hardwood"
    if code in {"S", "OS", "US", "SH", "GENC"}:
        return code, "CONIFEROUS", "conifer", "aggregate_conifer"
    return None


# ---------------------------------------------------------------------------
# Full-layer readers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DbfField:
    name: str
    type: str
    length: int
    decimals: int
    offset: int


def _read_dbf_header(path: Path) -> tuple[int, int, int, list[DbfField]]:
    with path.open("rb") as handle:
        header = handle.read(32)
        if len(header) != 32:
            raise DistributionError(f"truncated DBF header: {path}")
        record_count = struct.unpack("<I", header[4:8])[0]
        header_length = struct.unpack("<H", header[8:10])[0]
        record_length = struct.unpack("<H", header[10:12])[0]
        fields: list[DbfField] = []
        offset = 1
        while True:
            descriptor = handle.read(32)
            if not descriptor or descriptor[0] == 0x0D:
                break
            name = descriptor[:11].split(b"\x00", 1)[0].decode("latin1")
            field_type = chr(descriptor[11])
            length = descriptor[16]
            decimals = descriptor[17]
            fields.append(DbfField(name, field_type, length, decimals, offset))
            offset += length
    if offset != record_length:
        raise DistributionError(
            f"DBF record length {record_length} does not match field offsets {offset}: {path}"
        )
    return record_count, header_length, record_length, fields


def _parse_dbf_value(raw: bytes, field: DbfField) -> Any:
    text = raw.decode("latin1").strip()
    if not text:
        return None
    if field.type in {"N", "F"}:
        try:
            return float(text)
        except ValueError:
            return None
    return text


def _iter_dbf(path: Path, wanted: Sequence[str]) -> Iterator[dict[str, Any]]:
    record_count, header_length, record_length, fields = _read_dbf_header(path)
    by_name = {field.name: field for field in fields}
    missing = sorted(set(wanted).difference(by_name))
    if missing:
        raise DistributionError(f"DBF lacks fields {missing}: {path}")
    selected = [(name, by_name[name]) for name in wanted]
    with path.open("rb") as handle:
        handle.seek(header_length)
        for _ in range(record_count):
            raw = handle.read(record_length)
            if len(raw) != record_length:
                raise DistributionError(f"truncated DBF record in {path}")
            if raw[:1] == b"*":
                continue
            yield {
                name: _parse_dbf_value(raw[field.offset : field.offset + field.length], field)
                for name, field in selected
            }


# ---------------------------------------------------------------------------
# AB, MB, NS full-layer extraction
# ---------------------------------------------------------------------------


def _read_ab_avi(source_root: Path) -> pd.DataFrame:
    path = source_root / "ab_fgdb/Data/10TM_Offset/AlbertaVegetationInventoryCrown.gdb"
    if not path.exists():
        raise DistributionError(f"AB AVI FGDB not found: {path}")
    import pyogrio

    columns = [
        "SP1",
        "SP1_PER",
        "SP2",
        "SP2_PER",
        "SP3",
        "SP3_PER",
        "SP4",
        "SP4_PER",
        "SP5",
        "SP5_PER",
        "HEIGHT",
        "ORIGIN",
        "PHOTO_YR",
        "Shape_Area",
    ]
    frame = pyogrio.read_dataframe(
        path, layer="AVI_Crown", read_geometry=False, columns=columns
    )
    if frame.empty:
        raise DistributionError("AB AVI sample is empty")
    return frame


def _extract_ab(
    source_root: Path,
    lookup: Mapping[str, Mapping[str, tuple[str, str, str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    frame = _read_ab_avi(source_root)
    species_columns = [f"SP{index}" for index in range(1, 6)]
    proportion_columns = [f"SP{index}_PER" for index in range(1, 6)]
    area = pd.to_numeric(frame["Shape_Area"], errors="coerce")
    height = pd.to_numeric(frame["HEIGHT"], errors="coerce")
    origin = pd.to_numeric(frame["ORIGIN"], errors="coerce")
    photo_year = pd.to_numeric(frame["PHOTO_YR"], errors="coerce")
    age = photo_year - origin
    proportion_frame = frame[proportion_columns].apply(pd.to_numeric, errors="coerce")
    total_proportion = proportion_frame.clip(lower=0.0).sum(axis=1)

    code_map = {
        code: values[0]
        for code, values in lookup["AB"].items()
    }
    code_map.update(
        {
            "H": "BROADLEAF",
            "OH": "BROADLEAF",
            "UH": "BROADLEAF",
            "HS": "BROADLEAF",
            "IH": "BROADLEAF",
            "TH": "BROADLEAF",
            "UC": "BROADLEAF",
            "S": "CONIFEROUS",
            "OS": "CONIFEROUS",
            "US": "CONIFEROUS",
            "SH": "CONIFEROUS",
            "GENC": "CONIFEROUS",
        }
    )

    parts: list[pd.DataFrame] = []
    for species_column, proportion_column in zip(
        species_columns, proportion_columns, strict=True
    ):
        part = pd.DataFrame(
            {
                "raw_code": frame[species_column].astype(str).str.strip(),
                "proportion": proportion_frame[proportion_column].clip(lower=0.0),
                "area": area,
                "total_proportion": total_proportion,
                "height": height,
                "age": age,
            }
        )
        part = part[
            part["raw_code"].ne("")
            & part["raw_code"].ne("nan")
            & part["proportion"].gt(0.0)
            & part["total_proportion"].gt(0.0)
            & part["area"].gt(0.0)
        ]
        if not part.empty:
            parts.append(part)
    if not parts:
        raise DistributionError("AB AVI produced no species/proportion rows")
    melted = pd.concat(parts, ignore_index=True)
    melted["species_nfi"] = melted["raw_code"].str.upper().map(code_map)
    ignored_codes = (
        melted.loc[melted["species_nfi"].isna(), "raw_code"].value_counts().to_dict()
    )
    melted = melted.dropna(subset=["species_nfi"]).copy()
    if melted.empty:
        raise DistributionError("AB AVI produced no resolved species composition")
    melted["weight"] = (
        melted["area"] * melted["proportion"] / melted["total_proportion"]
    )

    composition_weights = melted.groupby("species_nfi")["weight"].sum()
    polygon_counts = melted.groupby("species_nfi").size()
    total_area = float(composition_weights.sum())
    composition_rows: list[dict[str, Any]] = []
    for nfi in sorted(composition_weights.index):
        composition_rows.append(
            {
                "province": "AB",
                "source_id": "ab_avi_crown",
                "raw_code": "",
                "species_nfi": nfi,
                "species_group": _species_group(nfi),
                "share": round(float(composition_weights[nfi]) / total_area, 8),
                "weight_basis": "polygon_area_x_species_proportion",
                "n_polygons": int(polygon_counts[nfi]),
                "source_role": "full_layer",
                "notes": "AVI SP1..SP5 x SP1_PER..SP5_PER, Shape_Area weighted",
            }
        )

    quantile_rows: list[dict[str, Any]] = []
    valid_height = melted["height"].gt(1.3) & melted["height"].le(100.0)
    height_frame = melted[valid_height]
    for nfi, group in height_frame.groupby("species_nfi", sort=True):
        quantile_rows.append(
            {
                "province": "AB",
                "source_id": "ab_avi_crown",
                "attribute": "height",
                "unit": "m",
                "species_nfi": nfi,
                "weighting": "polygon_area_x_species_proportion",
                **_quantile_stats(
                    group["height"].to_numpy(dtype=float),
                    group["weight"].to_numpy(dtype=float),
                ),
                "source_role": "full_layer",
                "notes": "AVI HEIGHT; photo-interpreted stand height",
            }
        )
    valid_age = melted["age"].gt(0.0) & melted["age"].le(300.0)
    age_frame = melted[valid_age]
    for nfi, group in age_frame.groupby("species_nfi", sort=True):
        quantile_rows.append(
            {
                "province": "AB",
                "source_id": "ab_avi_crown",
                "attribute": "age",
                "unit": "years",
                "species_nfi": nfi,
                "weighting": "polygon_area_x_species_proportion",
                **_quantile_stats(
                    group["age"].to_numpy(dtype=float),
                    group["weight"].to_numpy(dtype=float),
                ),
                "source_role": "full_layer",
                "notes": "PHOTO_YR - ORIGIN; stand origin age",
            }
        )
    if not height_frame.empty:
        quantile_rows.append(
            {
                "province": "AB",
                "source_id": "ab_avi_crown",
                "attribute": "height",
                "unit": "m",
                "species_nfi": "ALL",
                "weighting": "polygon_area_x_species_proportion",
                **_quantile_stats(
                    height_frame["height"].to_numpy(dtype=float),
                    height_frame["weight"].to_numpy(dtype=float),
                ),
                "source_role": "full_layer",
                "notes": "AVI HEIGHT, all resolved species",
            }
        )
    if not age_frame.empty:
        quantile_rows.append(
            {
                "province": "AB",
                "source_id": "ab_avi_crown",
                "attribute": "age",
                "unit": "years",
                "species_nfi": "ALL",
                "weighting": "polygon_area_x_species_proportion",
                **_quantile_stats(
                    age_frame["age"].to_numpy(dtype=float),
                    age_frame["weight"].to_numpy(dtype=float),
                ),
                "source_role": "full_layer",
                "notes": "PHOTO_YR - ORIGIN, all resolved species",
            }
        )
    summary = {
        "province": "AB",
        "source_ids": "ab_avi_crown",
        "composition_source": "AVI SP1..SP5 x SP1_PER..SP5_PER (area weighted)",
        "height_source": "AVI HEIGHT (area weighted)",
        "dbh_source": "absent; inferred from height with a donor-fit H-D equation",
        "age_source": "AVI PHOTO_YR - ORIGIN",
        "volume_source": "absent in AVI polygons",
        "n_records": len(frame),
        "notes": (
            f"{len(ignored_codes)} unmapped species codes ignored; "
            f"height valid {int(valid_height.sum())}; age valid {int(valid_age.sum())}"
        ),
    }
    return composition_rows, quantile_rows, [], summary


def _parse_mb_species_string(value: str) -> list[tuple[str, float]]:
    text = _text(value).upper()
    if not text:
        return []
    tokens: list[tuple[str, float]] = []
    for match in re.finditer(r"([A-Z]{1,3})(\d{1,2})", text):
        proportion = float(match.group(2))
        if proportion > 0.0:
            tokens.append((match.group(1), proportion))
    return tokens


def _read_mb_fri(source_root: Path) -> pd.DataFrame:
    path = source_root / "mb_fgdb/Manitoba_Forest_Inventory_1970s.gdb"
    if not path.exists():
        raise DistributionError(f"MB FRI FGDB not found: {path}")
    import pyogrio

    columns = ["SPECIES", "AGE_YRPHOTO", "YEARPHOTO", "AREA_HA"]
    frame = pyogrio.read_dataframe(
        path,
        layer="Manitoba_Forest_Inventory_1970s",
        read_geometry=False,
        columns=columns,
    )
    if frame.empty:
        raise DistributionError("MB FRI sample is empty")
    return frame


def _extract_mb(
    source_root: Path,
    lookup: Mapping[str, Mapping[str, tuple[str, str, str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    frame = _read_mb_fri(source_root)
    frame = frame.assign(row_id=np.arange(len(frame)))
    area = pd.to_numeric(frame["AREA_HA"], errors="coerce")
    age = pd.to_numeric(frame["AGE_YRPHOTO"], errors="coerce")
    tokens = (
        frame["SPECIES"]
        .astype(str)
        .str.upper()
        .str.findall(r"([A-Z]{1,3})(\d{1,2})")
    )
    exploded = frame.assign(tokens=tokens).explode("tokens", ignore_index=True)
    exploded = exploded[exploded["tokens"].notna()].copy()
    if exploded.empty:
        raise DistributionError("MB FRI produced no species tokens")
    exploded["raw_code"] = exploded["tokens"].str[0]
    exploded["proportion"] = pd.to_numeric(
        exploded["tokens"].str[1], errors="coerce"
    )
    exploded["area"] = area.to_numpy()[exploded["row_id"].to_numpy()]
    exploded["age"] = age.to_numpy()[exploded["row_id"].to_numpy()]
    exploded["total_proportion"] = exploded.groupby("row_id")["proportion"].transform(
        "sum"
    )
    exploded = exploded[
        exploded["proportion"].gt(0.0)
        & exploded["total_proportion"].gt(0.0)
        & exploded["area"].gt(0.0)
    ].copy()
    code_map = {code: values[0] for code, values in lookup["MB"].items()}
    code_map.update(
        {
            "H": "BROADLEAF",
            "OH": "BROADLEAF",
            "UH": "BROADLEAF",
            "HS": "BROADLEAF",
            "IH": "BROADLEAF",
            "TH": "BROADLEAF",
            "UC": "BROADLEAF",
            "S": "CONIFEROUS",
            "OS": "CONIFEROUS",
            "US": "CONIFEROUS",
            "SH": "CONIFEROUS",
            "GENC": "CONIFEROUS",
        }
    )
    exploded["species_nfi"] = exploded["raw_code"].map(code_map)
    ignored_codes = (
        exploded.loc[exploded["species_nfi"].isna(), "raw_code"].value_counts().to_dict()
    )
    exploded = exploded.dropna(subset=["species_nfi"]).copy()
    if exploded.empty:
        raise DistributionError("MB FRI produced no resolved species composition")
    exploded["weight"] = (
        exploded["area"] * exploded["proportion"] / exploded["total_proportion"]
    )
    composition_weights = exploded.groupby("species_nfi")["weight"].sum()
    polygon_counts = exploded.groupby("species_nfi")["row_id"].nunique()
    total_area = float(composition_weights.sum())
    composition_rows = [
        {
            "province": "MB",
            "source_id": "mb_fri_1970s",
            "raw_code": "",
            "species_nfi": nfi,
            "species_group": _species_group(nfi),
            "share": round(float(composition_weights[nfi]) / total_area, 8),
            "weight_basis": "polygon_area_x_species_proportion",
            "n_polygons": int(polygon_counts[nfi]),
            "source_role": "full_layer",
            "notes": "MB SPECIES string tokens (code+proportion tenths), AREA_HA weighted",
        }
        for nfi in sorted(composition_weights.index)
    ]
    quantile_rows: list[dict[str, Any]] = []
    valid_age = exploded["age"].gt(0.0) & exploded["age"].le(300.0)
    age_frame = exploded[valid_age]
    for nfi, group in age_frame.groupby("species_nfi", sort=True):
        quantile_rows.append(
            {
                "province": "MB",
                "source_id": "mb_fri_1970s",
                "attribute": "age",
                "unit": "years",
                "species_nfi": nfi,
                "weighting": "polygon_area_x_species_proportion",
                **_quantile_stats(
                    group["age"].to_numpy(dtype=float),
                    group["weight"].to_numpy(dtype=float),
                ),
                "source_role": "full_layer",
                "notes": "AGE_YRPHOTO; 1970s photo-interpreted stand age",
            }
        )
    if not age_frame.empty:
        quantile_rows.append(
            {
                "province": "MB",
                "source_id": "mb_fri_1970s",
                "attribute": "age",
                "unit": "years",
                "species_nfi": "ALL",
                "weighting": "polygon_area_x_species_proportion",
                **_quantile_stats(
                    age_frame["age"].to_numpy(dtype=float),
                    age_frame["weight"].to_numpy(dtype=float),
                ),
                "source_role": "full_layer",
                "notes": "AGE_YRPHOTO, all resolved species",
            }
        )
    summary = {
        "province": "MB",
        "source_ids": "mb_fri_1970s",
        "composition_source": "FRI SPECIES composition string, AREA_HA weighted",
        "height_source": "absent; donor measured height",
        "dbh_source": "absent; donor measured DBH",
        "age_source": "AGE_YRPHOTO (area weighted)",
        "volume_source": "absent in the open 1970s stand attributes",
        "n_records": len(frame),
        "notes": (
            f"{exploded['row_id'].nunique()} polygons with a parsed composition; "
            f"{int(valid_age.sum())} species-age observations; "
            f"{len(ignored_codes)} unmapped codes ignored"
        ),
    }
    return composition_rows, quantile_rows, [], summary


def _read_ns_shapefile_dbf(source_root: Path) -> Path:
    path = source_root / "ns_forest.dbf"
    if not path.is_file():
        raise DistributionError(f"NS forest inventory DBF not found: {path}")
    return path


def _extract_ns(
    source_root: Path,
    lookup: Mapping[str, Mapping[str, tuple[str, str, str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    path = _read_ns_shapefile_dbf(source_root)
    wanted = [
        "ID_EFOREST",
        "SPECIES",
        "SP1",
        "SP1P",
        "SP2",
        "SP2P",
        "SP3",
        "SP3P",
        "SP4",
        "SP4P",
        "HEIGHT",
        "TOTBA",
        "TOTMRVOL",
        "AVDI",
        "HECTARES",
    ]
    composition: dict[str, float] = defaultdict(float)
    species_polygon_counts: dict[str, int] = defaultdict(int)
    joint_rows: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    ignored_codes: dict[str, int] = defaultdict(int)
    valid_polygons = 0
    for record in _iter_dbf(path, wanted):
        hectares = _positive(record.get("HECTARES"), "HECTARES")
        height = _positive(record.get("HEIGHT"), "HEIGHT")
        diameter = _positive(record.get("AVDI"), "AVDI")
        volume = _positive(record.get("TOTMRVOL"), "TOTMRVOL")
        if hectares is None or height is None or height <= 1.3:
            continue
        if diameter is None or volume is None:
            continue
        species: list[tuple[str, float]] = []
        for index in range(1, 5):
            raw_code = _text(record.get(f"SP{index}"))
            proportion = _finite(record.get(f"SP{index}P"), f"SP{index}P")
            if raw_code and proportion is not None and proportion > 0.0:
                species.append((raw_code, proportion))
        if not species:
            continue
        total_proportion = math.fsum(proportion for _, proportion in species)
        if total_proportion <= 0.0:
            continue
        valid_polygons += 1
        for raw_code, proportion in species:
            resolved = _resolve_code("NS", raw_code, lookup)
            if resolved is None:
                ignored_codes[raw_code] += 1
                continue
            _, nfi, _, _ = resolved
            weight = hectares * proportion / total_proportion
            composition[nfi] += weight
            species_polygon_counts[nfi] += 1
            joint_rows[nfi].append((height, diameter, volume, weight))
    if not composition:
        raise DistributionError("NS inventory produced no species composition")
    total_area = math.fsum(composition.values())
    composition_rows = [
        {
            "province": "NS",
            "source_id": "ns_forest_inventory",
            "raw_code": "",
            "species_nfi": nfi,
            "species_group": _species_group(nfi),
            "share": round(weight / total_area, 8),
            "weight_basis": "polygon_hectares_x_species_proportion",
            "n_polygons": species_polygon_counts[nfi],
            "source_role": "full_layer",
            "notes": "NS SP1..SP4 x SP1P..SP4P, HECTARES weighted",
        }
        for nfi, weight in sorted(composition.items(), key=lambda item: (-item[1], item[0]))
    ]
    quantile_rows: list[dict[str, Any]] = []
    joint_output: list[dict[str, Any]] = []
    for nfi in sorted(joint_rows):
        rows = sorted(joint_rows[nfi], key=lambda item: (item[0], item[1], item[2]))
        if len(rows) < MIN_SPECIES_OBSERVATIONS:
            continue
        height_values = [row[0] for row in rows]
        diameter_values = [row[1] for row in rows]
        volume_values = [row[2] for row in rows]
        weights = [row[3] for row in rows]
        stats_height = _quantile_stats(height_values, weights)
        stats_diameter = _quantile_stats(diameter_values, weights)
        stats_volume = _quantile_stats(volume_values, weights)
        quantile_rows.append(
            {
                "province": "NS",
                "source_id": "ns_forest_inventory",
                "attribute": "height",
                "unit": "m",
                "species_nfi": nfi,
                "weighting": "polygon_hectares_x_species_proportion",
                **stats_height,
                "source_role": "full_layer",
                "notes": "NS HEIGHT; first-storey stand height",
            }
        )
        quantile_rows.append(
            {
                "province": "NS",
                "source_id": "ns_forest_inventory",
                "attribute": "dbh",
                "unit": "cm",
                "species_nfi": nfi,
                "weighting": "polygon_hectares_x_species_proportion",
                **stats_diameter,
                "source_role": "full_layer",
                "notes": "NS AVDI; average total diameter used as the stand QMD proxy",
            }
        )
        quantile_rows.append(
            {
                "province": "NS",
                "source_id": "ns_forest_inventory",
                "attribute": "volume",
                "unit": "m3/ha",
                "species_nfi": nfi,
                "weighting": "polygon_hectares_x_species_proportion",
                **stats_volume,
                "source_role": "full_layer",
                "notes": "NS TOTMRVOL; merchantable volume",
            }
        )
        row: dict[str, Any] = {
            "province": "NS",
            "source_id": "ns_forest_inventory",
            "species_nfi": nfi,
            "weighting": "polygon_hectares_x_species_proportion",
            "n": len(rows),
            "notes": "joint stand rows sorted by HEIGHT; AVDI and TOTMRVOL carried with the same row",
        }
        for quantile in QUANTILE_GRID:
            key = f"p{round(quantile * 100):02d}"
            row[f"{key}_height_m"] = round(_weighted_quantile(height_values, weights, quantile), 6)
            row[f"{key}_dbh_cm"] = round(_weighted_quantile(diameter_values, weights, quantile), 6)
            row[f"{key}_volume_m3ha"] = round(
                _weighted_quantile(volume_values, weights, quantile), 6
            )
        joint_output.append(row)

    overall_rows = [item for rows in joint_rows.values() for item in rows]
    if overall_rows:
        overall_height = [item[0] for item in overall_rows]
        overall_diameter = [item[1] for item in overall_rows]
        overall_volume = [item[2] for item in overall_rows]
        overall_weights = [item[3] for item in overall_rows]
        for attribute, unit, values, note in (
            ("height", "m", overall_height, "NS HEIGHT, all resolved species"),
            ("dbh", "cm", overall_diameter, "NS AVDI, all resolved species"),
            ("volume", "m3/ha", overall_volume, "NS TOTMRVOL, all resolved species"),
        ):
            quantile_rows.append(
                {
                    "province": "NS",
                    "source_id": "ns_forest_inventory",
                    "attribute": attribute,
                    "unit": unit,
                    "species_nfi": "ALL",
                    "weighting": "polygon_hectares_x_species_proportion",
                    **_quantile_stats(values, overall_weights),
                    "source_role": "full_layer",
                    "notes": note,
                }
            )
    summary = {
        "province": "NS",
        "source_ids": "ns_forest_inventory",
        "composition_source": "NS SP1..SP4 x SP1P..SP4P (hectare weighted)",
        "height_source": "NS HEIGHT (hectare weighted)",
        "dbh_source": "NS AVDI (hectare weighted; average total diameter)",
        "age_source": "absent",
        "volume_source": "NS TOTMRVOL (hectare weighted)",
        "n_records": 1_004_111,
        "notes": (
            f"{valid_polygons} valid forest polygons with species, height, AVDI, "
            f"TOTMRVOL and hectares; joint rows retained for species with "
            f">= {MIN_SPECIES_OBSERVATIONS} observations"
        ),
    }
    return composition_rows, quantile_rows, joint_output, summary


# ---------------------------------------------------------------------------
# Sample-based SK and PE extraction
# ---------------------------------------------------------------------------


def _audit_sample(name: str) -> pd.DataFrame:
    path = AUDIT_DIR / "samples" / name
    if not path.is_file():
        raise DistributionError(f"audit sample not found: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        raise DistributionError(f"audit sample is empty: {path}")
    return frame


def _extract_sk(
    lookup: Mapping[str, Mapping[str, tuple[str, str, str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    frame = _audit_sample("sk_fri_forest_stands_sample.csv")
    height_values: list[float] = []
    height_weights: list[float] = []
    diameter_values: list[float] = []
    diameter_weights: list[float] = []
    volume_values: list[float] = []
    volume_weights: list[float] = []
    basal_values: list[float] = []
    basal_weights: list[float] = []
    stems_values: list[float] = []
    stems_weights: list[float] = []
    group_weights: dict[str, float] = defaultdict(float)
    valid = 0
    for record in frame.to_dict(orient="records"):
        if _text(record.get("TYPE")).upper() not in {"", "FOR"}:
            continue
        hectares = _positive(record.get("HECTARES"), "HECTARES")
        height = _positive(record.get("HEIGHT"), "HEIGHT")
        diameter = _positive(record.get("QMD"), "QMD")
        volume = _positive(record.get("GBVOL"), "GBVOL")
        if hectares is None or height is None or height <= 1.3 or diameter is None:
            continue
        if volume is None:
            continue
        valid += 1
        height_values.append(height)
        height_weights.append(hectares)
        diameter_values.append(diameter)
        diameter_weights.append(hectares)
        volume_values.append(volume)
        volume_weights.append(hectares)
        basal = _positive(record.get("BA"), "BA")
        if basal is not None:
            basal_values.append(basal)
            basal_weights.append(hectares)
        stems = _positive(record.get("MSTEMS"), "MSTEMS")
        if stems is not None:
            stems_values.append(stems)
            stems_weights.append(hectares)
        group = _text(record.get("CSG")).upper()
        if group == "S":
            group_weights["conifer"] += hectares
        elif group == "H":
            group_weights["deciduous"] += hectares
        elif group in {"SH", "HS"}:
            group_weights["conifer"] += 0.5 * hectares
            group_weights["deciduous"] += 0.5 * hectares
    if valid == 0:
        raise DistributionError("SK FRI sample produced no valid stands")
    total_group = math.fsum(group_weights.values())
    if total_group <= 0.0:
        raise DistributionError("SK FRI sample produced no CSG group weight")
    composition_rows = [
        {
            "province": "SK",
            "source_id": "sk_fri_forest_stands",
            "raw_code": "CSG:" + ("S" if group == "conifer" else "H"),
            "species_nfi": "CONIFEROUS" if group == "conifer" else "BROADLEAF",
            "species_group": group,
            "share": round(weight / total_group, 8),
            "weight_basis": "polygon_hectares",
            "n_polygons": 0,
            "source_role": "sample_500",
            "notes": "SK FSTAND publishes only a CSG softwood/hardwood group; no explicit species code",
        }
        for group, weight in sorted(group_weights.items(), key=lambda item: (-item[1], item[0]))
    ]
    quantile_rows: list[dict[str, Any]] = []
    specs = [
        ("height", "m", height_values, height_weights, "SK HEIGHT; Lorey mean height"),
        ("dbh", "cm", diameter_values, diameter_weights, "SK QMD; quadratic mean diameter"),
        ("volume", "m3/ha", volume_values, volume_weights, "SK GBVOL; gross total volume"),
        ("basal_area", "m2/ha", basal_values, basal_weights, "SK BA"),
        ("stems", "stems/ha", stems_values, stems_weights, "SK MSTEMS; merchantable stems"),
    ]
    for attribute, unit, values, weights, note in specs:
        if not values:
            continue
        quantile_rows.append(
            {
                "province": "SK",
                "source_id": "sk_fri_forest_stands",
                "attribute": attribute,
                "unit": unit,
                "species_nfi": "ALL",
                "weighting": "polygon_hectares",
                **_quantile_stats(values, weights),
                "source_role": "sample_500",
                "notes": note,
            }
        )
    summary = {
        "province": "SK",
        "source_ids": "sk_fri_forest_stands",
        "composition_source": "FRI CSG softwood/hardwood group (hectare weighted); SCANFI resolves species inside each group",
        "height_source": "FRI HEIGHT (hectare weighted)",
        "dbh_source": "FRI QMD (hectare weighted)",
        "age_source": "absent",
        "volume_source": "FRI GBVOL / GMVOL8 (hectare weighted)",
        "n_records": len(frame),
        "notes": (
            f"{valid} valid FOR stands in the checked-in 500-row sample; "
            "the full FSTAND REST layer is not redistributed here"
        ),
    }
    return composition_rows, quantile_rows, [], summary


def _extract_pe_clui(
    lookup: Mapping[str, Mapping[str, tuple[str, str, str]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    frame = _audit_sample("pe_clui_2010_forest_sample.csv")
    composition: dict[str, float] = defaultdict(float)
    species_polygon_counts: dict[str, int] = defaultdict(int)
    height_values: dict[str, list[float]] = defaultdict(list)
    height_weights: dict[str, list[float]] = defaultdict(list)
    ignored_codes: dict[str, int] = defaultdict(int)
    valid = 0
    for record in frame.to_dict(orient="records"):
        if _text(record.get("LANDUSE")).upper() != "FOR":
            continue
        hectares = _positive(record.get("HECTARES"), "HECTARES")
        if hectares is None:
            continue
        species: list[tuple[str, float]] = []
        for index in range(1, 6):
            raw_code = _text(record.get(f"COVER{index}"))
            proportion = _finite(record.get(f"PER{index}"), f"PER{index}")
            if raw_code and proportion is not None and proportion > 0.0:
                species.append((raw_code, proportion))
        if not species:
            continue
        total_proportion = math.fsum(proportion for _, proportion in species)
        if total_proportion <= 0.0:
            continue
        valid += 1
        polygon_height = _positive(record.get("HEIGHT"), "HEIGHT")
        valid_height = polygon_height is not None and polygon_height > 1.3
        for raw_code, proportion in species:
            resolved = _resolve_code("PE", raw_code, lookup)
            if resolved is None:
                ignored_codes[raw_code] += 1
                continue
            _, nfi, _, _ = resolved
            weight = hectares * proportion / total_proportion
            composition[nfi] += weight
            species_polygon_counts[nfi] += 1
            if valid_height:
                height_values[nfi].append(float(polygon_height))
                height_weights[nfi].append(weight)
    if not composition:
        raise DistributionError("PE CLUI produced no species composition")
    total_area = math.fsum(composition.values())
    composition_rows = [
        {
            "province": "PE",
            "source_id": "pe_clui_2010_forest",
            "raw_code": "",
            "species_nfi": nfi,
            "species_group": _species_group(nfi),
            "share": round(weight / total_area, 8),
            "weight_basis": "polygon_hectares_x_species_proportion",
            "n_polygons": species_polygon_counts[nfi],
            "source_role": "sample_500",
            "notes": "PE CLUI COVER1..COVER5 x PER1..PER5, HECTARES weighted",
        }
        for nfi, weight in sorted(composition.items(), key=lambda item: (-item[1], item[0]))
    ]
    quantile_rows: list[dict[str, Any]] = []
    for nfi in sorted(height_values):
        if len(height_values[nfi]) < MIN_SPECIES_OBSERVATIONS:
            continue
        quantile_rows.append(
            {
                "province": "PE",
                "source_id": "pe_clui_2010_forest",
                "attribute": "height",
                "unit": "m",
                "species_nfi": nfi,
                "weighting": "polygon_hectares_x_species_proportion",
                **_quantile_stats(height_values[nfi], height_weights[nfi]),
                "source_role": "sample_500",
                "notes": "PE CLUI HEIGHT; photo-interpreted stand height",
            }
        )
    summary = {
        "province": "PE",
        "source_ids": "pe_clui_2010_forest;pe_efi_2020",
        "composition_source": "CLUI COVER1..COVER5 x PER1..PER5 (hectare weighted)",
        "height_source": "CLUI HEIGHT by species (hectare weighted); EFI topht for volume context",
        "dbh_source": "EFI bamerch/tphmerch QMD inference (no CLUI DBH)",
        "age_source": "absent",
        "volume_source": "EFI gtv (m3/ha)",
        "n_records": len(frame),
        "notes": (
            f"{valid} CLUI FOR polygons with composition; "
            f"{len(ignored_codes)} unmapped codes ignored"
        ),
    }
    return composition_rows, quantile_rows, [], summary


def _extract_pe_efi() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    frame = _audit_sample("pe_efi_2020_sample.csv")
    height_values: list[float] = []
    diameter_values: list[float] = []
    volume_values: list[float] = []
    weights: list[float] = []
    basal_values: list[float] = []
    stems_values: list[float] = []
    valid = 0
    for record in frame.to_dict(orient="records"):
        height = _positive(record.get("topht"), "topht")
        basal = _positive(record.get("bamerch"), "bamerch")
        stems = _positive(record.get("tphmerch"), "tphmerch")
        volume = _positive(record.get("gtv"), "gtv")
        if height is None or height <= 1.3 or basal is None or stems is None:
            continue
        if volume is None:
            continue
        diameter = 100.0 * math.sqrt(basal / (stems * math.pi / 4.0))
        if not math.isfinite(diameter) or diameter <= 0.0:
            continue
        valid += 1
        area = _positive(record.get("SHAPE__Area"), "SHAPE__Area") or 400.0
        height_values.append(height)
        diameter_values.append(diameter)
        volume_values.append(volume)
        basal_values.append(basal)
        stems_values.append(stems)
        weights.append(area)
    if valid == 0:
        raise DistributionError("PE EFI sample produced no valid cells")
    quantile_rows: list[dict[str, Any]] = []
    specs = [
        ("height", "m", height_values, "PE EFI topht"),
        ("dbh", "cm", diameter_values, "PE EFI QMD inferred as 100*sqrt(bamerch/(tphmerch*pi/4))"),
        ("volume", "m3/ha", volume_values, "PE EFI gtv; gross total volume"),
        ("basal_area", "m2/ha", basal_values, "PE EFI bamerch"),
        ("stems", "stems/ha", stems_values, "PE EFI tphmerch"),
    ]
    for attribute, unit, values, note in specs:
        quantile_rows.append(
            {
                "province": "PE",
                "source_id": "pe_efi_2020",
                "attribute": attribute,
                "unit": unit,
                "species_nfi": "ALL",
                "weighting": "cell_area",
                **_quantile_stats(values, weights),
                "source_role": "sample_500",
                "notes": note,
            }
        )
    summary = {
        "province": "PE",
        "source_ids": "pe_efi_2020",
        "composition_source": "none; species from CLUI",
        "height_source": "EFI topht (cell area weighted)",
        "dbh_source": "EFI QMD inferred from bamerch and tphmerch",
        "age_source": "absent",
        "volume_source": "EFI gtv (cell area weighted)",
        "n_records": len(frame),
        "notes": f"{valid} valid EFI cells in the checked-in 500-row sample",
    }
    return quantile_rows, [], summary


# ---------------------------------------------------------------------------
# Provenance and artifact assembly
# ---------------------------------------------------------------------------


def _audit_provenance() -> dict[str, dict[str, str]]:
    manifest = pd.read_csv(AUDIT_DIR / "manifest.csv", dtype=str, keep_default_na=False)
    sources = pd.read_csv(AUDIT_DIR / "source_table.csv", dtype=str, keep_default_na=False)
    by_source = {row["source_id"]: row for row in sources.to_dict(orient="records")}
    result: dict[str, dict[str, str]] = {}
    for row in manifest.to_dict(orient="records"):
        source = by_source.get(row["source_id"], {})
        digest = row.get("full_artifact_sha256") or row.get("sample_sha256", "")
        result[row["source_id"]] = {
            "source_id": row["source_id"],
            "province": row["province"],
            "source_name": source.get("source_name", ""),
            "publisher": source.get("publisher", ""),
            "licence": source.get("licence", ""),
            "source_url": source.get("source_url", ""),
            "retrieval_date": row.get("retrieval_date", RETRIEVAL_DATE),
            "full_or_sample": "full_layer" if row.get("full_artifact_sha256") else "sample",
            "n_records": row.get("sample_rows", ""),
            "source_sha256": digest,
            "fields_used": source.get("species_field", ""),
            "notes": source.get("notes", ""),
        }
    return result


def _inference_equations() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "equation_id": "PE-QMD-FROM-BA-TPH",
            "province": "PE",
            "species_nfi": "ALL",
            "form": "QMD_cm = 100 * sqrt(bamerch_m2ha / (tphmerch * pi / 4))",
            "coefficient_a": "",
            "coefficient_b": "",
            "n": 0,
            "rmse": "",
            "source": "PE EFI2020 bamerch and tphmerch fields; mass-balance definition of quadratic mean diameter",
            "notes": "Inference is algebraic, not fitted; label inferred_dbh.",
        },
        {
            "equation_id": "AB-HD-DONOR-LOGLOG",
            "province": "AB",
            "species_nfi": "ALL",
            "form": "ln(DBH_cm) = a + b * ln(H_m - 1.3)",
            "coefficient_a": "",
            "coefficient_b": "",
            "n": 0,
            "rmse": "",
            "source": "log-log DBH-on-height model fitted to measured BC/QC/NB/YT donor stems, by NFI species or species group",
            "notes": "Fitted coefficients are written per species in this table at generation time.",
        },
    ]
    return rows


def _write_readme(
    path: Path,
    composition_rows: Sequence[Mapping[str, Any]],
    quantile_rows: Sequence[Mapping[str, Any]],
    joint_rows: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    provenance: Mapping[str, Mapping[str, str]],
) -> None:
    lines = [
        "# Provincial open inventory distributions v1",
        "",
        f"Retrieval date: {RETRIEVAL_DATE}",
        "",
        "This artifact converts the attributes that open provincial inventories",
        "actually publish into small, area-weighted distribution tables for E2 v2.",
        "It does not ingest full layers into the model and does not claim that any",
        "one inventory is complete.",
        "",
        "## Sources and fields",
        "",
        "| Province | Source | Fields used | Coverage |",
        "|---|---|---|---|",
        "| AB | AVI Crown | SP1..SP5, SP1_PER..SP5_PER, HEIGHT, ORIGIN, PHOTO_YR, Shape_Area | full 788,810-polygon FGDB |",
        "| SK | FRI Forest Stands | CSG, HEIGHT, BA, QMD, MSTEMS, GBVOL, GMVOL8, HECTARES | checked-in 500-stand sample |",
        "| MB | FRI 1970s | SPECIES composition string, AGE_YRPHOTO, AREA_HA | full 582,601-polygon FGDB |",
        "| NS | Forest Inventory | SP1..SP4, SP1P..SP4P, HEIGHT, AVDI, TOTBA, TOTMRVOL, HECTARES | full 1,004,111-record DBF |",
        "| PE | CLUI 2010 + EFI2020 | COVER1..COVER5, PER1..PER5, HEIGHT; topht, bamerch, tphmerch, gtv | checked-in 500-row samples |",
        "| ON, NL, NT, NU | no open stand attributes | gap recorded; SCANFI composition + measured donor geometry in E2 v2 | not distribution-informed |",
        "",
        "## Files",
        "",
        "- `species_composition.csv`: area- or hectare-weighted species shares by province.",
        "- `attribute_quantiles.csv`: weighted p05..p95, mean, median and SD by province, source and attribute.",
        "- `joint_quantiles.csv`: NS species-specific joint stand quantiles (height, AVDI, volume) retained from the same polygon.",
        "- `inference_equations.csv`: documented DBH inference equations.",
        "- `province_summary.csv`: one row per province with the fields actually used.",
        "- `source_provenance.csv`: source, licence, URL, retrieval date and SHA-256.",
        "",
        "## Labels",
        "",
        "- Area-weighted shares use polygon area/hectares times the published species proportion.",
        "- `HEIGHT` and `AGE` are photo-interpreted stand attributes, not tree measurements.",
        "- SK QMD is a stand quadratic mean diameter, not a tree-list joint distribution.",
        "- PE QMD is inferred from EFI merchantable basal area and merchantable stems.",
        "- MB has no open height or DBH; E2 v2 uses measured donor geometry for those joints.",
        "",
        "No request, DUA, restricted data or official OSeCAN input is used.",
        "",
        "## Summary",
        "",
        "| Province | Composition source | Height | DBH | Age | Volume |",
        "|---|---|---|---|---|---|",
    ]
    for summary in summaries:
        lines.append(
            f"| {summary['province']} | {summary['composition_source']} | "
            f"{summary['height_source']} | {summary['dbh_source']} | "
            f"{summary['age_source']} | {summary['volume_source']} |"
        )
    lines.extend(
        [
            "",
            f"Rows written: {len(composition_rows)} composition, "
            f"{len(quantile_rows)} quantile, {len(joint_rows)} joint, "
            f"{len(provenance)} provenance.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build(
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    source_root: Path | None = None,
) -> dict[str, Any]:
    source_root = Path(source_root or REPO_ROOT / "tmp/pia")
    if not source_root.is_dir():
        raise DistributionError(
            f"source root not found: {source_root}; pass --source-root with the "
            "AB/MB FGDB and NS DBF downloads"
        )
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    lookup = _load_species_lookup()

    ab_composition, ab_quantiles, _, ab_summary = _extract_ab(source_root, lookup)
    sk_composition, sk_quantiles, _, sk_summary = _extract_sk(lookup)
    mb_composition, mb_quantiles, _, mb_summary = _extract_mb(source_root, lookup)
    ns_composition, ns_quantiles, ns_joint, ns_summary = _extract_ns(source_root, lookup)
    pe_composition, pe_height_quantiles, _, pe_clui_summary = _extract_pe_clui(lookup)
    pe_efi_quantiles, _, pe_efi_summary = _extract_pe_efi()

    composition_rows = [
        *ab_composition,
        *sk_composition,
        *mb_composition,
        *ns_composition,
        *pe_composition,
    ]
    quantile_rows = [
        *ab_quantiles,
        *sk_quantiles,
        *mb_quantiles,
        *ns_quantiles,
        *pe_height_quantiles,
        *pe_efi_quantiles,
    ]
    summaries = [
        ab_summary,
        sk_summary,
        mb_summary,
        ns_summary,
        pe_clui_summary,
        pe_efi_summary,
        {
            "province": "ON",
            "source_ids": "on_fri_packaged_products",
            "composition_source": "gap; SCANFI v2 composition used in E2 v2",
            "height_source": "gap; donor measured height",
            "dbh_source": "gap; donor measured DBH",
            "age_source": "gap",
            "volume_source": "gap",
            "n_records": 0,
            "notes": "FRI stand-attribute packages require a LIDS order; only the open catalogue was audited.",
        },
        {
            "province": "NL",
            "source_ids": "nl_land_cover_forest",
            "composition_source": "gap; land cover has FORESTTYPE only; SCANFI composition used",
            "height_source": "gap; donor measured height",
            "dbh_source": "gap; donor measured DBH",
            "age_source": "gap",
            "volume_source": "gap",
            "n_records": 0,
            "notes": "Open polygon layer exposes FORESTTYPE, STOCKING and SITECODE only.",
        },
        {
            "province": "NT",
            "source_ids": "nt_wooded_areas",
            "composition_source": "gap; ATLAS vegetation type only; SCANFI composition used",
            "height_source": "gap; donor measured height",
            "dbh_source": "gap; donor measured DBH",
            "age_source": "gap",
            "volume_source": "gap",
            "n_records": 0,
            "notes": "ATLAS Wooded Areas is community-scale broad vegetation mapping; FVI/MVI are not directly open.",
        },
        {
            "province": "NU",
            "source_ids": "nu_no_inventory",
            "composition_source": "gap; SCANFI composition used",
            "height_source": "gap; donor measured height",
            "dbh_source": "gap; donor measured DBH",
            "age_source": "gap",
            "volume_source": "gap",
            "n_records": 0,
            "notes": "No open territorial stand inventory was identified.",
        },
    ]

    provenance = _audit_provenance()
    provenance_rows = [provenance[source_id] for source_id in provenance]

    composition_columns = [
        "province",
        "source_id",
        "raw_code",
        "species_nfi",
        "species_group",
        "share",
        "weight_basis",
        "n_polygons",
        "source_role",
        "notes",
    ]
    quantile_columns = [
        "province",
        "source_id",
        "attribute",
        "unit",
        "species_nfi",
        "weighting",
        "n",
        "p05",
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
        "p95",
        "min",
        "max",
        "mean",
        "median",
        "sd",
        "source_role",
        "notes",
    ]
    joint_columns = [
        "province",
        "source_id",
        "species_nfi",
        "weighting",
        "n",
        *[
            f"p{round(quantile * 100):02d}_{suffix}"
            for quantile in QUANTILE_GRID
            for suffix in ("height_m", "dbh_cm", "volume_m3ha")
        ],
        "notes",
    ]
    summary_columns = [
        "province",
        "source_ids",
        "composition_source",
        "height_source",
        "dbh_source",
        "age_source",
        "volume_source",
        "n_records",
        "notes",
    ]
    provenance_columns = [
        "source_id",
        "province",
        "source_name",
        "publisher",
        "licence",
        "source_url",
        "retrieval_date",
        "full_or_sample",
        "n_records",
        "source_sha256",
        "fields_used",
        "notes",
    ]
    _write_csv(out_dir / "species_composition.csv", composition_rows, composition_columns)
    _write_csv(out_dir / "attribute_quantiles.csv", quantile_rows, quantile_columns)
    _write_csv(out_dir / "joint_quantiles.csv", ns_joint, joint_columns)
    _write_csv(
        out_dir / "inference_equations.csv",
        _inference_equations(),
        ["equation_id", "province", "species_nfi", "form", "coefficient_a", "coefficient_b", "n", "rmse", "source", "notes"],
    )
    _write_csv(out_dir / "province_summary.csv", summaries, summary_columns)
    _write_csv(out_dir / "source_provenance.csv", provenance_rows, provenance_columns)
    _write_readme(
        out_dir / "README.md",
        composition_rows,
        quantile_rows,
        ns_joint,
        summaries,
        provenance,
    )
    manifest = {
        "artifact": ARTIFACT_ID,
        "retrieval_date": RETRIEVAL_DATE,
        "inputs": {
            "species_code_mapping": _sha256(SPECIES_MAPPING_PATH),
            "audit_source_table": _sha256(AUDIT_DIR / "source_table.csv"),
            "audit_manifest": _sha256(AUDIT_DIR / "manifest.csv"),
        },
        "outputs": {
            name: _sha256(out_dir / name)
            for name in (
                "species_composition.csv",
                "attribute_quantiles.csv",
                "joint_quantiles.csv",
                "inference_equations.csv",
                "province_summary.csv",
                "source_provenance.csv",
                "README.md",
            )
        },
        "counts": {
            "composition_rows": len(composition_rows),
            "quantile_rows": len(quantile_rows),
            "joint_rows": len(ns_joint),
            "provinces": len(summaries),
        },
        "no_request_or_dua": True,
    }
    import json

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-root", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = build(args.out_dir, source_root=args.source_root)
    print(manifest["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
