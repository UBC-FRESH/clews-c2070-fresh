"""BC VRI Vegetation Resources Inventory -> CASFRI-shape adapter.

Reads BC Vegetation Resources Inventory (VRI) Forest Vegetation Composite
- Rank 1 Layer polygons (the ``VEG_COMP_LYR_R1_POLY`` feature class
distributed by DataBC as an ESRI File Geodatabase) and emits one
CASFRI-shape row per polygon stand-region. The output rows slot into the
same diagnostic supply generator that ``fresh_fibre.casfri_qc_reader``
feeds for quebec IEQM.

**Boundary discipline.** The adapter reads the BC VRI File Geodatabase
(BC Open Government Licence - British Columbia) and the already-vendored
CASFRI v5 translation lookups under
``data/raw/casfri_v5/translation_tables/``. It does **not** vendor or
invoke the CASFRI GPL-3.0 conversion scripts. See
``metadata/source-crosswalks/qc_casfri_v5_schema.md`` for the stylistic
mirror and ``metadata/source-reviews/casfri_v5_schema.md`` for the
cross-jurisdiction schema review.

**Optional dependencies.** The ``.gdb`` reader path uses ``fiona`` when
available and degrades to a pure-Python BC VRI CSV reader when it is not
installed (matching the QC adapter's CSV-first discipline). The
geometry-area helper does not require ``shapely``; when no planar
coordinates are reachable it returns ``None``, and the source GDB's
pre-computed ``SHAPE_AREA`` attribute is used as a fast-path value when
present (units: square metres).

**Namespace.** The BC-specific ``species_cd_1..6`` outputs are raw
producer codes with the team's jurisdictional ``BC_`` prefix reserved
for downstream ``fresh_fibre.species_canonical``. That module applies
the cross-jurisdiction species canonicalization via the
``bc_species_codes`` column of ``species_code_mapping.csv``. This
adapter does **not** re-implement that translation - it only emits the
producer-code row shape so downstream code can resolve.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Sequence

try:
    import fiona
except ImportError:  # pragma: no cover - fiona is optional
    fiona = None  # type: ignore[assignment]

from fresh_fibre.schema import read_rows, write_rows


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

LOGGER = logging.getLogger("fresh_fibre.bc_vri_reader")

DEFAULT_BC_FEATURE_CLASS: str = "VEG_COMP_LYR_R1_POLY"
"""Default feature class name inside the BC VRI File Geodatabase archive."""

DEFAULT_LOOKUP_DIR: Path = Path("data/raw/casfri_v5/translation_tables")
"""Vendored CASFRI translation-table directory shared by all readers."""

DEFAULT_LOOKUP_FILENAME: str = "bc_vri01_lyr.csv"
"""BC VRI layer-translation lookup CSV (referenced by species_canonical)."""

DEFAULT_OUTPUT_DIR: Path = Path("data/raw/casfri_v5/bc_vri_translated")
"""Default coalesced CSV output directory."""

DEFAULT_OUTPUT_FILENAME: str = "bc_vri_casfri_shape.csv"
"""Default single-output filename when no FMU grouping is configured."""

BC_VRI_EPSG: int = 3005
"""NAD83 / BC Albers planar CRS (units = metres)."""

SPECIES_RANK_COUNT: int = 6
"""BC VRI stores up to six ranked species per polygon."""

JURISDICTION: str = "BC"
SOURCE_LABEL: str = "BC_VRI"
INV_PREFIX: str = "BC_VRI"  # cas_id namespace prefix

WRAPPER_COLUMNS: tuple[str, ...] = (
    "row_id",
    "source_raw_row_id",
    "source_raw_table",
)
"""Stable wrapper columns added to every BC VRI translated row."""

BC_OUTPUT_COLUMNS: tuple[str, ...] = (
    "cas_id",
    "poly_id",
    "map_id",
    "inventory_year",
    "bec_zone",
    "bec_subzone",
    "nutrient_moisture",
    "slope_position",
    "crown_closure_pct",
    "basal_area_m2_ha",
    "stems_ha",
    "species_cd_1",
    "species_cd_2",
    "species_cd_3",
    "species_cd_4",
    "species_cd_5",
    "species_cd_6",
    "height_m",
    "age_yr",
    "site_index_m",
    "stand_type",
    "land_cover_class",
    "jurisdiction",
    "source",
    "source_vintage",
    "coord_x",
    "coord_y",
    "geometry_wkt",
    "area_ha",
    "species_composition",
    *WRAPPER_COLUMNS,
)
"""30 CASFRI-shape columns + 3 wrapper provenance columns."""


# ---------------------------------------------------------------------------
# BC VRI field alias map (BC -> CASFRI)
# ---------------------------------------------------------------------------

# Each logical BC VRI attribute is mapped to a tuple of acceptable
# producer-side field names (case-sensitive variants seen in historical
# VRI snapshots: pre-2016, post-2016, and the 2024 vintage). The first
# non-empty match wins. Column names follow the DataBC Data Dictionary
# but tolerate the lower-cased CSV-export convention that the QC reader
# also accepts (see ``QC_STAND_COLUMNS``).
BC_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "poly_id":             ("POLY_ID", "poly_id", "STAND_ID", "stand_id"),
    "map_id":              ("MAP_ID", "map_id", "MAP_SHEET_ID", "nts_map", "NTS_MAP"),
    "proj_open_year":      ("PROJ_OPEN_YEAR", "proj_open_year", "INV_YEAR"),
    "proj_close_year":     ("PROJ_CLOSE_YEAR", "proj_close_year"),
    "bec_zone":            ("BEC_ZONE", "bec_zone", "ZONE"),
    "bec_subzone":         ("BEC_SUBZONE", "bec_subzone", "SUBZONE"),
    "nutrient_moisture":   ("NUTRIENT_MOISTURE", "nutrient_moisture", "NUT_MOIST"),
    "slope_position":      ("SLOPE_POSITION", "slope_position", "SLOPE_POS"),
    "crown_closure_pct":   ("CROWN_CLOSURE_PCT", "crown_closure_pct", "CC_PCT", "CC"),
    "basal_area_m2_ha":    ("BASAL_AREA_M2_HA", "basal_area_m2_ha", "BA_HA", "BASAL_AREA"),
    "stems_ha":            ("STEMS_HA", "stems_ha", "STEMS_PHA", "TPH"),
    "height_m":            ("HEIGHT_M", "height_m", "S1_AVGHT", "TOP_HEIGHT"),
    "age_yr":              ("AGE_YR", "age_yr", "PROJ_AGE_1", "STAND_AGE"),
    "site_index_m":        ("SITE_INDEX_M", "site_index_m", "SITE_INDEX", "SI_M"),
    "stand_type":          ("STAND_TYPE", "stand_type", "S_TYPE"),
    # Primary BC VRI land cover field; legacy no-suffix variant tolerated.
    "land_cover_class":    ("LAND_COVER_CLASS_CD", "land_cover_class_cd", "LAND_COVER_CLASS"),

    "fri_key":             ("FRI_KEY", "fri_key", "COMPOSITE_KEY"),
    "object_id":           ("OBJECTID", "objectid", "FID"),
    "shape_area_m2":       ("SHAPE_AREA", "shape_area", "Shape_Area"),
    "shape_length_m":      ("SHAPE_LENGTH", "shape_length", "Shape_Length"),
}

# Six ranked species-pct slot aliases - generated once so callers can
# address ``species_cd_1`` through ``species_cd_6`` consistently.
for _rank in range(1, SPECIES_RANK_COUNT + 1):
    BC_FIELD_ALIASES[f"species_cd_{_rank}"] = (
        f"SPECIES_CD_{_rank}",
        f"species_cd_{_rank}",
        f"SPCS_CD_{_rank}",
    )
    BC_FIELD_ALIASES[f"species_pct_{_rank}"] = (
        f"SPECIES_PCT_{_rank}",
        f"species_pct_{_rank}",
        f"SPCS_PCT_{_rank}",
    )


# ---------------------------------------------------------------------------
# Pure helpers (Atomic Predictability)
# ---------------------------------------------------------------------------


def safe_str(value: object) -> str:
    """Coerce a raw BC VRI cell value to a stripped string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def safe_float(value: object, default: float | None = None) -> float | None:
    """Parse a numeric BC VRI cell to ``float``; empty/invalid -> ``default``."""
    text = safe_str(value)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def safe_int(value: object, default: int | None = None) -> int | None:
    """Parse a numeric BC VRI cell to ``int``; empty/invalid -> ``default``."""
    parsed = safe_float(value, default=default)
    if parsed is None:
        return default
    try:
        return int(parsed)
    except (ValueError, TypeError):
        return default


def first_present(row: Mapping[str, object], aliases: Sequence[str]) -> str:
    """Return the first non-empty BC VRI cell value across ``aliases``."""
    for alias in aliases:
        if alias in row:
            text = safe_str(row[alias])
            if text:
                return text
    return ""


def url_safe_token(value: str, *, max_len: int = 32) -> str:
    """Sanitize a BC VRI id token so it can sit safely inside a CASFRI ``cas_id``."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", safe_str(value))
    return (cleaned or "anon")[:max_len]


def make_cas_id(poly_id: str, map_id: str) -> str:
    """Produce a BC-namespaced CASFRI ``cas_id`` of the form ``BC_VRI_<map>_<poly>``."""
    return f"{INV_PREFIX}_{url_safe_token(map_id)}_{url_safe_token(poly_id)}"
    # e.g. "BC_VRI_092J07_12345" when POLY_ID == "12345" and MAP_ID == "092J07"


def stable_hash(parts: Sequence[str]) -> str:
    """Deterministic, collision-resistant identifier (used for ``row_id``)."""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _ring_signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Return the shoelace-formula signed area of one polygon ring."""
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x_i, y_i = ring[i][0], ring[i][1]
        x_j, y_j = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        s += x_i * y_j - x_j * y_i
    return s * 0.5


def _geometry_polygon_area(geom: Mapping[str, object]) -> float:
    """Return planar polygon area in native units (typically square metres)."""
    coords = geom.get("coordinates") or []
    kind = safe_str(geom.get("type", ""))
    if kind == "Polygon":
        if not coords:
            return 0.0
        exterior, *holes = coords
        return abs(
            _ring_signed_area(exterior) - sum(abs(_ring_signed_area(h)) for h in holes),
        )
    if kind == "MultiPolygon":
        total = 0.0
        for poly in coords:
            if not poly:
                continue
            exterior, *holes = poly
            total += abs(
                _ring_signed_area(exterior)
                - sum(abs(_ring_signed_area(h)) for h in holes),
            )
        return abs(total)
    return 0.0


def area_ha_from_geometry(
    geom: Mapping[str, object] | None,
    *,
    fallback_area_m2: float | None = None,
) -> float | None:
    """Compute hectares from a GeoJSON-style geometry mapping.

    Args:
        geom: GeoJSON-style ``{"type": "Polygon", "coordinates": [...]}`` or
            ``"MultiPolygon"``. ``None`` is accepted and propagates ``None``.
        fallback_area_m2: When supplied and positive, used directly (BC VRI
            ESRI File Geodatabases expose ``SHAPE_AREA`` in square metres).
            Wins over a re-computation when present.

    Returns:
        Area in hectares, or ``None`` when neither fallback nor a parseable
        planar geometry is available.
    """
    if fallback_area_m2 is not None and float(fallback_area_m2) > 0:
        return float(fallback_area_m2) / 10_000.0
    if not geom:
        return None
    native_area = _geometry_polygon_area(geom)
    if native_area <= 0.0:
        return None
    return native_area / 10_000.0  # sq m -> ha (BC VRI metric)


def geometry_to_wkt(geom: Mapping[str, object] | None) -> str | None:
    """Convert a GeoJSON-style geometry mapping to a WKT string."""
    if not geom:
        return None
    kind = safe_str(geom.get("type", ""))
    coords = geom.get("coordinates") or []

    def _fmt_ring(ring: Sequence[Sequence[float]]) -> str:
        return ",".join(f"{pair[0]} {pair[1]}" for pair in ring)

    if kind == "Polygon":
        rings = ", ".join(f"({_fmt_ring(ring)})" for ring in coords)
        return f"POLYGON ({rings})"
    if kind == "MultiPolygon":
        polys = ",".join(
            f"({', '.join(f'({_fmt_ring(ring)})' for ring in poly)})"
            for poly in coords
        )
        return f"MULTIPOLYGON ({polys})"
    return None


def centroid_xy(
    geom: Mapping[str, object] | None,
) -> tuple[float | None, float | None]:
    """Return the planar (x, y) centroid of a single Polygon, else ``(None, None)``.

    Multi-polygon centroids are deliberately not returned: callers that
    need a multipolygon centroid should reproject downstream. Reporting
    ``None`` here preserves the adapter's "Fail Fast, Fail Loud"
    boundary - we never invent a coordinate.
    """
    if not geom:
        return None, None
    if safe_str(geom.get("type", "")) != "Polygon":
        return None, None
    coords = geom.get("coordinates") or []
    if not coords:
        return None, None
    exterior = coords[0]
    n = len(exterior)
    if n < 3:
        return None, None
    cx = 0.0
    cy = 0.0
    signed = 0.0
    for i in range(n):
        x_i, y_i = exterior[i][0], exterior[i][1]
        x_j, y_j = exterior[(i + 1) % n][0], exterior[(i + 1) % n][1]
        cross = x_i * y_j - x_j * y_i
        cx += (x_i + x_j) * cross
        cy += (y_i + y_j) * cross
        signed += cross
    if signed == 0.0:
        return None, None
    signed *= 0.5
    return cx / (6.0 * signed), cy / (6.0 * signed)


def species_resolve(row: Mapping[str, object]) -> list[tuple[str, float]]:
    """Extract up to ``SPECIES_RANK_COUNT`` ranked species (code, pct) pairs.

    Skips empty species-code slots so downstream consumers do not see
    phantom zero-percent pairs. ``pct`` defaults to ``0.0`` when the
    upstream cell carries no percentage (consistent with BC VRI's
    frequent partial-record practice - the docstring in the task brief
    calls out "lots of nulls").
    """
    pairs: list[tuple[str, float]] = []
    for rank in range(1, SPECIES_RANK_COUNT + 1):
        code = first_present(row, BC_FIELD_ALIASES[f"species_cd_{rank}"])
        if not code:
            continue
        pct_raw = first_present(row, BC_FIELD_ALIASES[f"species_pct_{rank}"])
        pct = safe_float(pct_raw, default=0.0) or 0.0
        pairs.append((code.upper(), pct))
    return pairs


def species_composition_string(
    pairs: Sequence[tuple[str, float]],
) -> str:
    """Render a ``species_composition`` cell as ``CODE:PCT;CODE:PCT;...``.

    Filters out zero-percent pairs so the cell carries only material
    species. Stable ordering matches ``species_resolve`` (rank order).
    """
    pieces: list[str] = []
    for code, pct in pairs:
        if pct <= 0.0:
            continue
        pieces.append(f"{code}:{pct:g}")
    return ";".join(pieces)


# ---------------------------------------------------------------------------
# I/O: lookup table holder (lightweight, mirrors QcLookupTables)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BcLookupTables:
    """One fully-loaded set of CASFRI translation lookups for BC VRI."""

    species_by_source_val: dict[str, dict[str, str]] = field(default_factory=dict)
    """BC producer codes -> layer/species/per translations from
    ``bc_vri01_lyr.csv``. May be empty when the lookup file is absent;
    species canonicalization is downstream in ``species_canonical``."""

    @classmethod
    def from_csv_dir(cls, table_dir: Path) -> "BcLookupTables":
        """Load BC VRI lookups from ``table_dir``. Tolerant of missing files."""
        layer_path = table_dir / DEFAULT_LOOKUP_FILENAME
        species_index: dict[str, dict[str, str]] = {}
        if layer_path.exists():
            for row in read_rows(layer_path):
                key = safe_str(row.get("source_val", ""))
                if not key:
                    continue
                species_index[key] = {
                    col: safe_str(row.get(col, "")) for col in row
                }
        else:
            LOGGER.debug(
                "BC VRI lookup file not found at %s; species canonicalization "
                "deferred to downstream species_canonical (no-op here).",
                layer_path,
            )
        return cls(species_by_source_val=species_index)


# ---------------------------------------------------------------------------
# I/O: BC VRI source readers
# ---------------------------------------------------------------------------


def _require_gdb_path(gdb_path: Path) -> Path:
    """Validate a BC VRI .gdb path before opening it (Fail Fast)."""
    if not gdb_path.exists():
        raise FileNotFoundError(f"BC VRI archive does not exist: {gdb_path}")
    candidates = [gdb_path, *gdb_path.parent.glob(f"{gdb_path.name}.gdb")]
    has_gdb = gdb_path.suffix.lower() == ".gdb" or any(p.is_dir() for p in candidates)
    if not has_gdb:
        raise FileNotFoundError(
            f"BC VRI archive at {gdb_path} does not look like an ESRI .gdb "
            f"(no .gdb directory found in same path).",
        )
    return gdb_path


def read_bc_vri_gdb(
    gdb_path: Path,
    feature_class: str = DEFAULT_BC_FEATURE_CLASS,
) -> Iterator[tuple[dict[str, object], str | None]]:
    """Yield ``(raw_feature, object_id)`` pairs from a BC VRI ``.gdb`` archive.

    The feature class is ``feature_class`` (default ``VEG_COMP_LYR_R1_POLY``).
    Requires ``fiona``. When ``fiona`` is not installed, prefer
    ``read_bc_vri_csv`` (CSV export produced from a prior materialization).

    Args:
        gdb_path: path to the BC VRI File Geodatabase directory.
        feature_class: feature class layer to read.

    Yields:
        Tuple of (raw_feature_dict, object_id_string). ``object_id`` is
        sourced from the first matching alias in ``BC_FIELD_ALIASES``.

    Raises:
        RuntimeError: when ``fiona`` is not importable.
        FileNotFoundError: when the path / feature class cannot be located.
    """
    if fiona is None:
        raise RuntimeError(
            "fiona is required to read BC VRI File Geodatabases; install it "
            "with `pip install fiona` or supply a pre-exported CSV via "
            "read_bc_vri_csv().",
        )
    _require_gdb_path(gdb_path)
    LOGGER.info(
        "Opening BC VRI archive %s; feature_class=%s",
        gdb_path,
        feature_class,
    )
    with fiona.open(str(gdb_path), layer=feature_class) as src:
        for feature in src:
            properties = dict(feature.get("properties") or {})
            geometry = feature.get("geometry")
            if geometry is not None:
                properties.setdefault("__geometry__", geometry)
            object_id = first_present(
                properties, BC_FIELD_ALIASES["object_id"],
            )
            yield properties, (object_id or None)


def read_bc_vri_csv(
    csv_path: Path,
) -> Iterator[tuple[dict[str, object], str | None]]:
    """Yield ``(raw_feature, object_id)`` pairs from a BC VRI CSV export.

    A pre-exported CSV is the engine path used when ``fiona`` is not
    available in the runtime. Tolerant of extra columns - the adapter
    looks values up by alias list, not by exact column match.

    Args:
        csv_path: path to a UTF-8 BC VRI CSV (one polygon per row).

    Yields:
        Tuple of (raw_row_dict, object_id_string).
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"BC VRI CSV does not exist: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_dict: dict[str, object] = dict(row)
            object_id = first_present(
                row_dict, BC_FIELD_ALIASES["object_id"],
            )
            yield row_dict, (object_id or None)


# ---------------------------------------------------------------------------
# Pure translator: BC VRI row -> CASFRI-shape dict
# ---------------------------------------------------------------------------


def _read_geometry(row: Mapping[str, object]) -> Mapping[str, object] | None:
    """Pull a geometry mapping off a BC VRI feature row (GDB path only)."""
    geom = row.get("__geometry__")
    if isinstance(geom, Mapping):
        return geom
    return None


def translate_row_to_casfri(
    row: Mapping[str, object],
    *,
    raw_objectid: str | None = None,
) -> dict[str, object]:
    """Translate one BC VRI polygon row into one CASFRI-shape row.

    The translated row carries raw producer species codes plus stable
    identifiers (``cas_id``, ``row_id``). Downstream
    ``fresh_fibre.species_canonical`` performs the BC-prefixed canonical
    lookup; this adapter only emits the producer-side fields so the
    downstream stage can see what the source actually said.
    """
    poly_id = first_present(row, BC_FIELD_ALIASES["poly_id"]) or (
        raw_objectid or "anon"
    )
    map_id = first_present(row, BC_FIELD_ALIASES["map_id"])
    object_id = raw_objectid or first_present(
        row, BC_FIELD_ALIASES["object_id"],
    )
    shape_area_m2 = safe_float(
        first_present(row, BC_FIELD_ALIASES["shape_area_m2"]),
        default=None,
    )
    geometry = _read_geometry(row)
    area_ha = area_ha_from_geometry(geometry, fallback_area_m2=shape_area_m2)

    proj_open = safe_int(
        first_present(row, BC_FIELD_ALIASES["proj_open_year"]), default=None,
    )
    proj_close = safe_int(
        first_present(row, BC_FIELD_ALIASES["proj_close_year"]), default=None,
    )

    species_pairs = species_resolve(row)
    species_codes = [code for code, _ in species_pairs]
    species_pcts = [pct for _, pct in species_pairs]

    cx, cy = centroid_xy(geometry)

    cas_id = make_cas_id(poly_id, map_id)
    vintage = first_present(row, BC_FIELD_ALIASES["fri_key"])

    # Wrapper provenance - the wrapper columns are populated without
    # requiring any optional lookup or external state.
    row_id = stable_hash([cas_id, object_id or "no-oid", str(poly_id)])
    source_raw_row_id = object_id or poly_id or ""
    source_raw_table = "VEG_COMP_LYR_R1_POLY"

    return {
        "cas_id": cas_id,
        "poly_id": poly_id,
        "map_id": map_id,
        "inventory_year": proj_open,
        "bec_zone": first_present(row, BC_FIELD_ALIASES["bec_zone"]),
        "bec_subzone": first_present(row, BC_FIELD_ALIASES["bec_subzone"]),
        "nutrient_moisture": first_present(
            row, BC_FIELD_ALIASES["nutrient_moisture"],
        ),
        "slope_position": first_present(row, BC_FIELD_ALIASES["slope_position"]),
        "crown_closure_pct": safe_float(
            first_present(row, BC_FIELD_ALIASES["crown_closure_pct"]), default=None,
        ),
        "basal_area_m2_ha": safe_float(
            first_present(row, BC_FIELD_ALIASES["basal_area_m2_ha"]), default=None,
        ),
        "stems_ha": safe_float(
            first_present(row, BC_FIELD_ALIASES["stems_ha"]), default=None,
        ),
        "species_cd_1": species_codes[0] if len(species_codes) >= 1 else "",
        "species_cd_2": species_codes[1] if len(species_codes) >= 2 else "",
        "species_cd_3": species_codes[2] if len(species_codes) >= 3 else "",
        "species_cd_4": species_codes[3] if len(species_codes) >= 4 else "",
        "species_cd_5": species_codes[4] if len(species_codes) >= 5 else "",
        "species_cd_6": species_codes[5] if len(species_codes) >= 6 else "",
        "height_m": safe_float(
            first_present(row, BC_FIELD_ALIASES["height_m"]), default=None,
        ),
        "age_yr": safe_int(
            first_present(row, BC_FIELD_ALIASES["age_yr"]), default=None,
        ),
        "site_index_m": safe_float(
            first_present(row, BC_FIELD_ALIASES["site_index_m"]), default=None,
        ),
        "stand_type": first_present(row, BC_FIELD_ALIASES["stand_type"]),
        "land_cover_class": first_present(
            row, BC_FIELD_ALIASES["land_cover_class"],
        ),
        "jurisdiction": JURISDICTION,
        "source": SOURCE_LABEL,
        "source_vintage": vintage if vintage else (
            f"{proj_open}/{proj_close}" if proj_open or proj_close else ""
        ),
        "coord_x": cx,
        "coord_y": cy,
        "geometry_wkt": geometry_to_wkt(geometry),
        "area_ha": area_ha,
        "species_composition": species_composition_string(species_pairs),
        # --- wrapper columns ---
        "row_id": row_id,
        "source_raw_row_id": source_raw_row_id,
        "source_raw_table": source_raw_table,
    }


def process_bc_vri(
    gdb_path: Path,
    lookup_dir: Path = DEFAULT_LOOKUP_DIR,
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    feature_class: str = DEFAULT_BC_FEATURE_CLASS,
    fmu_key_field: str | None = None,
    csv_fallback_path: Path | None = None,
) -> dict[str, object]:
    """Stream BC VRI polygons -> coalesced CSV(s), optionally partitioned by FMU key.

    Args:
        gdb_path: path to the BC VRI File Geodatabase directory.
        lookup_dir: directory holding ``bc_vri01_lyr.csv``.
        out_dir: directory to write coalesced output CSV(s).
        feature_class: BC VRI feature class to read.
        fmu_key_field: optional name of a BC VRI producer field whose
            value partitions polygons (e.g. ``"TFL_ID"`` if a BC Timber
            Sale Licence pre-join was performed upstream). When ``None``,
            a single coalesced CSV is written.
        csv_fallback_path: if supplied (and ``fiona`` is not installed),
            this CSV is read instead of the ``.gdb`` archive.

    Returns:
        Summary dict with ``raw_polygon_count``, ``output_paths``, and
        (when partitioned) the list of FMU group keys.

    Raises:
        FileNotFoundError: when neither ``gdb_path`` nor
            ``csv_fallback_path`` is reachable.
        ValueError: when zero polygons are produced.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Tolerant lookup load (warns-and-continues when the layer lookup
    # file is absent - species resolution is downstream).
    try:
        BcLookupTables.from_csv_dir(lookup_dir)
    except FileNotFoundError as exc:
        LOGGER.warning("Skipping BC lookup load: %s", exc)

    # Reader dispatch:
    #   - explicit ``csv_fallback_path`` always wins (caller is choosing CSV).
    #   - else ``fiona`` opens the .gdb.
    #   - else RuntimeError; caller gets a clear failure mode.
    if csv_fallback_path is not None:
        iterator = read_bc_vri_csv(csv_fallback_path)
    elif fiona is not None:
        iterator = read_bc_vri_gdb(gdb_path, feature_class=feature_class)
    else:
        raise RuntimeError(
            "BC VRI reader requires either fiona (for .gdb archives) or "
            "--csv-fallback-path (a pre-exported BC VRI CSV). Neither supplied.",
        )

    # We coalesce into per-FMU buckets if requested; otherwise one bucket.
    # Python dicts preserve insertion order, so output CSV filenames stay
    # stable across runs when the input order is stable.
    buckets: dict[str, list[dict[str, object]]] = {"ALL": []}
    raw_count = 0
    for raw_row, object_id in iterator:
        raw_count += 1
        casfri_row = translate_row_to_casfri(raw_row, raw_objectid=object_id)
        if fmu_key_field:
            group_key = safe_str(raw_row.get(fmu_key_field, "ALL")) or "ALL"
        else:
            group_key = "ALL"
        buckets.setdefault(group_key, []).append(casfri_row)

    if raw_count == 0:
        raise ValueError(
            f"No BC VRI polygons yielded (gdb_path={gdb_path}, "
            f"feature_class={feature_class}, csv_fallback={csv_fallback_path})",
        )
    if not any(buckets.values()):
        raise ValueError(
            "BC VRI adapter produced zero bucketed rows - input iterator "
            "returned feature objects that did not translate.",
        )

    written_paths: list[Path] = []
    for group_key, rows in buckets.items():
        if not rows:
            continue
        if group_key == "ALL":
            out_path = out_dir / DEFAULT_OUTPUT_FILENAME
        else:
            out_path = out_dir / f"bc_vri_casfri_{url_safe_token(group_key, max_len=64)}.csv"
        write_rows(out_path, list(BC_OUTPUT_COLUMNS), rows)
        written_paths.append(out_path)
        LOGGER.info("Wrote %d BC VRI rows to %s", len(rows), out_path)

    return {
        "input_path": str(gdb_path),
        "feature_class": feature_class,
        "raw_polygon_count": raw_count,
        "fmu_groups": [k for k, rows in buckets.items() if rows],
        "output_paths": [str(p) for p in written_paths],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (kept separate for testability)."""
    parser = argparse.ArgumentParser(
        prog="fresh_fibre.bc_vri_reader",
        description=(
            "Stream BC VRI polygons from a DataBC File Geodatabase "
            "into CASFRI-shape coalesced CSV(s)."
        ),
    )
    parser.add_argument(
        "--gdb-path",
        type=Path,
        required=True,
        help="Path to the BC VRI File Geodatabase (VEG_COMP_LYR_R1_POLY layer).",
    )
    parser.add_argument(
        "--lookup-dir",
        type=Path,
        default=DEFAULT_LOOKUP_DIR,
        help="Directory holding bc_vri01_lyr.csv (CASFRI lookups).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the coalesced output CSV(s).",
    )
    parser.add_argument(
        "--feature-class",
        default=DEFAULT_BC_FEATURE_CLASS,
        help="Feature class layer inside the .gdb archive.",
    )
    parser.add_argument(
        "--fmu-key-field",
        default=None,
        help=(
            "Optional BC VRI producer field whose value partitions polygons "
            "(e.g. 'TFL_ID'). When omitted, writes a single coalesced CSV."
        ),
    )
    parser.add_argument(
        "--csv-fallback-path",
        type=Path,
        default=None,
        help=(
            "Pre-exported CSV path used when fiona is not installed. "
            "Required for the fiona-free runtime."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry: process <gdb_path> -> coalesced CSV(s)."""
    parser = _build_argparser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    try:
        result = process_bc_vri(
            gdb_path=args.gdb_path,
            lookup_dir=args.lookup_dir,
            out_dir=args.out_dir,
            feature_class=args.feature_class,
            fmu_key_field=args.fmu_key_field,
            csv_fallback_path=args.csv_fallback_path,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"bc_vri_reader: {exc}", file=sys.stderr)
        return 1
    print(
        f"bc_vri_reader: translated {result['raw_polygon_count']} BC VRI polygons "
        f"from {result['input_path']} into {len(result['output_paths'])} CSV file(s) "
        f"under {args.out_dir}; fmu_groups={result['fmu_groups']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
