#!/usr/bin/env python3
"""Download, verify, extract, and parse public BFEC/Woodstock model bundles.

The pipeline is intentionally transactional per unit:

* downloads land in a temporary file and are renamed only after a size check;
* bundles extract into a temporary directory and are renamed only on success;
* each parsed unit writes its own CSV/JSON transaction before aggregation.

Raw ZIP bundles and extracted model directories stay outside the repository.
Only non-spatial public derivatives, a manifest, and a report are published.
Confidentiality-notice units are parsed but their inventory-derived tables are
written to ``restricted/`` and never aggregated into the public CSVs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import time
import traceback
import urllib.request
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path, PurePosixPath

import shapefile  # pyshp
from pyproj import CRS

from ws3.forest import ForestModel, read_model_text


VOLUME_CONIFER = "yv_g_gr"
VOLUME_TOTAL = "yv_s"
HEIGHT = "yh_s"
AGE_GRID = tuple(range(0, 201, 5))
CURVE_COLUMNS = (
    "unit",
    "development_type",
    "table_id",
    "species_group",
    "age",
    "volume_conifer",
    "volume_deciduous",
    "volume_total",
    "height",
)
DT_COLUMNS = (
    "unit",
    "dt_key",
    "theme_codes",
    "polygon_count",
    "inventory_area_ha",
    "stratum_labels",
    "table_id",
    "in_model",
    "productive",
    "non_forest",
    "match_ambiguous",
)
CROSSWALK_COLUMNS = (
    ("unit", "polygon_id")
    + tuple("THEME%d" % i for i in range(1, 21))
    + ("STRATE", "area_ha", "CRS")
)
CONFIDENTIAL_MARKERS = (
    "confidentiel",
    "autorisation écrite",
    "autorisation ecrite",
    "communauté autochtone",
    "communaute autochtone",
)
NON_FOREST_MARKERS = (
    "non forest",
    "non_forest",
    "non-forest",
    "non forestier",
    "non_forestier",
    "improductif",
    "eau",
    "water",
    "urbain",
    "route",
    "infrastructure",
    "tourbi",
    "denud",
    "lande",
    "sans production",
    "non boisé",
    "non boise",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_to_human(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0:
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%.1f PiB" % size


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def ws3_provenance() -> dict:
    """Record the parser version and git commit without failing the run."""
    source = Path(__file__).resolve()
    repo = source.parent.parent.parent / "ws3"
    commit = None
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    try:
        from ws3 import __version__ as ws3_version
    except ImportError:
        ws3_version = None
    return {
        "ws3_version": ws3_version,
        "ws3_git_commit": commit,
        "extractor_sha256": sha256_file(source),
    }


def download_unit(spec: dict, downloads_root: Path, attempts: int = 4) -> dict:
    """Download one bundle to the scratch root, verifying listed size."""
    destination = downloads_root / spec["file"]
    expected_bytes = spec["bytes"]
    if destination.exists():
        actual_bytes = destination.stat().st_size
        if expected_bytes is None or actual_bytes == expected_bytes:
            return {
                "unit": spec["unit"],
                "status": "reused",
                "path": str(destination),
                "bytes": actual_bytes,
                "sha256": sha256_file(destination),
                "runtime_s": 0.0,
            }
        destination.unlink()

    temporary = downloads_root / (spec["file"] + ".part-%d" % os.getpid())
    last_error: Exception | None = None
    started = utc_now()
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(spec["url"], timeout=180) as response, open(temporary, "wb") as handle:
                shutil.copyfileobj(response, handle, length=1 << 20)
            actual_bytes = temporary.stat().st_size
            if expected_bytes is not None and actual_bytes != expected_bytes:
                raise ValueError(
                    "size mismatch for %s: expected %d, got %d"
                    % (spec["file"], expected_bytes, actual_bytes)
                )
            digest = sha256_file(temporary)
            os.replace(temporary, destination)
            return {
                "unit": spec["unit"],
                "status": "downloaded",
                "path": str(destination),
                "bytes": actual_bytes,
                "sha256": digest,
                "runtime_s": round((utc_now() - started).total_seconds(), 3),
            }
        except Exception as exc:  # retry network and short-read failures
            last_error = exc
            if temporary.exists():
                temporary.unlink()
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError("download failed for %s: %s" % (spec["unit"], last_error))


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    """Extract members only when their paths stay inside destination."""
    for member in archive.infolist():
        member_path = PurePosixPath(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("unsafe zip member path: %s" % member.filename)
    archive.extractall(destination)


def extract_unit(spec: dict, downloads_root: Path, bundles_root: Path) -> dict:
    """Extract one verified bundle transactionally and locate its model root."""
    zip_path = downloads_root / spec["file"]
    if not zip_path.exists():
        raise FileNotFoundError("missing downloaded bundle %s" % zip_path.name)
    digest = sha256_file(zip_path)
    unit_dir = bundles_root / spec["unit"]
    marker = unit_dir / ".complete.json"
    if marker.exists():
        previous = json.loads(marker.read_text())
        if previous.get("zip_sha256") == digest:
            return {
                "unit": spec["unit"],
                "status": "reused",
                "model_root": previous["model_root"],
                "bytes": previous.get("extracted_bytes"),
                "runtime_s": 0.0,
            }

    temporary = bundles_root / (".tmp-%s-%s" % (spec["unit"], uuid.uuid4().hex))
    temporary.mkdir(parents=True)
    started = utc_now()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract(archive, temporary)
        model_files = sorted(temporary.glob("*.lan"))
        if not model_files:
            model_files = sorted(temporary.rglob("*.lan"))
        if len(model_files) != 1:
            raise ValueError(
                "expected exactly one .lan model file, found %d in %s"
                % (len(model_files), spec["file"])
            )
        model_root = model_files[0].parent.relative_to(temporary)
        payload = {
            "unit": spec["unit"],
            "zip_file": spec["file"],
            "zip_sha256": digest,
            "model_root": str(model_root),
            "extracted_bytes": directory_bytes(temporary),
            "extracted_at": utc_now().isoformat(),
        }
        (temporary / ".complete.json").write_text(json.dumps(payload, indent=2) + "\n")
        if unit_dir.exists():
            shutil.rmtree(unit_dir)
        os.replace(temporary, unit_dir)
        return {
            "unit": spec["unit"],
            "status": "extracted",
            "model_root": str(model_root),
            "bytes": payload["extracted_bytes"],
            "runtime_s": round((utc_now() - started).total_seconds(), 3),
        }
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def parse_theme_info(lan_path: Path) -> tuple[list[str], dict[str, str]]:
    """Return theme labels and first-theme code descriptions."""
    text = read_model_text(lan_path)
    labels: list[str] = []
    descriptions: dict[str, str] = {}
    in_first_theme = False
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("*THEME"):
            if in_first_theme:
                break
            in_first_theme = True
            parts = stripped.split(None, 2)
            labels.append(parts[2].strip() if len(parts) > 2 else "theme1")
            continue
        if not in_first_theme:
            continue
        if stripped.startswith("*"):
            break
        if not stripped or stripped.startswith(";") or stripped.startswith("{") or stripped.startswith("}"):
            continue
        code = stripped.split(None, 1)[0].lower()
        description = stripped.split(None, 1)[1] if len(stripped.split(None, 1)) > 1 else ""
        description = description.partition(";")[0].strip().lower()
        if code and code not in descriptions:
            descriptions[code] = description
    return labels, descriptions


def crs_label(prj_path: Path) -> tuple[str, bool, str | None]:
    if not prj_path.exists():
        return "unknown", False, "missing .prj"
    try:
        crs = CRS.from_wkt(read_model_text(prj_path))
    except Exception as exc:
        return "unknown", False, "unreadable .prj: %s" % exc
    epsg = crs.to_epsg()
    label = "EPSG:%d" % epsg if epsg else crs.to_string()
    return label, bool(crs.is_projected), None


def dbf_encoding(carte: Path) -> str:
    cpg = next((path for path in carte.iterdir() if path.suffix.lower() == ".cpg"), None)
    if cpg is None:
        return "cp1252"
    label = read_model_text(cpg).strip().lower()
    if "utf" in label:
        return "utf-8"
    if "1252" in label or "ansi" in label:
        return "cp1252"
    if "8859" in label:
        return "latin-1"
    return "cp1252"


def field_map(fields: list[str]) -> dict[str, int]:
    return {name.upper(): index for index, name in enumerate(fields)}


def numeric_value(value, context: str) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\xa0", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        if "," in text and "." not in text:
            return float(text.replace(",", "."))
        raise ValueError("%s is not numeric: %r" % (context, value)) from None


def find_carte_dir(bundle_dir: Path) -> Path:
    """Locate the single Carte directory, allowing a wrapper directory."""
    direct = bundle_dir / "Carte"
    if direct.is_dir():
        return direct
    matches = sorted(path for path in bundle_dir.rglob("Carte") if path.is_dir())
    if len(matches) != 1:
        raise FileNotFoundError(
            "expected one Carte/ directory in %s, found %d" % (bundle_dir, len(matches))
        )
    return matches[0]


def locate_inventories(carte: Path) -> list[tuple[Path, Path, Path]]:
    """Return every shapefile/DBF/PRJ triple in Carte, case-insensitively."""
    if not carte.is_dir():
        raise FileNotFoundError("missing Carte/ directory in %s" % carte.parent)
    shp_files = sorted(
        path for path in carte.iterdir()
        if path.is_file() and path.suffix.lower() == ".shp"
    )
    if not shp_files:
        raise FileNotFoundError("no .shp in %s" % carte)
    inventories = []
    for shp_path in shp_files:
        dbf_path = _sibling_with_suffix(shp_path, ".dbf")
        if dbf_path is None:
            raise FileNotFoundError("missing %s.dbf" % shp_path.stem)
        prj_path = _sibling_with_suffix(shp_path, ".prj")
        inventories.append((shp_path, dbf_path, prj_path))
    return inventories


def _sibling_with_suffix(path: Path, suffix: str) -> Path | None:
    for candidate in path.parent.iterdir():
        if candidate.is_file() and candidate.stem.lower() == path.stem.lower() and candidate.suffix.lower() == suffix:
            return candidate
    return None


def read_inventory(
    shp_path: Path,
    dbf_path: Path,
    unit: str,
    crs: str,
    writer: csv.DictWriter,
    polygon_start: int = 1,
) -> tuple[dict[tuple[str, ...], dict], int, float]:
    """Write the polygon crosswalk while aggregating inventory by theme key."""
    reader = shapefile.Reader(
        shp=str(shp_path),
        dbf=str(dbf_path),
        encoding=dbf_encoding(shp_path.parent),
        encodingErrors="replace",
    )
    try:
        fields = [field[0] for field in reader.fields[1:]]
        lookup = field_map(fields)
        theme_indexes = []
        for theme in range(1, 21):
            name = "THEME%d" % theme
            if name not in lookup:
                raise ValueError("inventory DBF is missing %s" % name)
            theme_indexes.append(lookup[name])
        area_index = next(
            (lookup[name] for name in ("SUPERFICIE", "SUPERFICIE_HA", "AREA_HA") if name in lookup),
            None,
        )
        if area_index is None:
            raise ValueError("inventory DBF is missing a SUPERFICIE/area field")
        strate_index = lookup.get("STRATE")

        aggregated: dict[tuple[str, ...], dict] = {}
        polygon_count = 0
        inventory_area_ha = 0.0
        for polygon_id, record in enumerate(reader.iterRecords(), start=polygon_start):
            themes = []
            for index in theme_indexes:
                value = record[index]
                text = "" if value is None else str(value).strip().lower()
                themes.append(text or "na")
            area_ha = numeric_value(record[area_index], "polygon %d area" % polygon_id)
            strate = ""
            if strate_index is not None and record[strate_index] is not None:
                strate = str(record[strate_index]).strip()
            key = tuple(themes)
            aggregate = aggregated.get(key)
            if aggregate is None:
                aggregate = {
                    "polygon_count": 0,
                    "area_ha": 0.0,
                    "strata": set(),
                }
                aggregated[key] = aggregate
            aggregate["polygon_count"] += 1
            aggregate["area_ha"] += area_ha
            if strate:
                aggregate["strata"].add(strate)
            row = {"unit": unit, "polygon_id": polygon_id, "area_ha": round(area_ha, 6), "CRS": crs}
            row.update({"THEME%d" % (index + 1): themes[index] for index in range(20)})
            row["STRATE"] = strate
            writer.writerow(row)
            polygon_count += 1
            inventory_area_ha += area_ha
        return aggregated, polygon_count, inventory_area_ha
    finally:
        reader.close()


def curve_signature(model: ForestModel, key: tuple[str, ...]) -> str | None:
    """Stable table id for the target yield tuple of a development type."""
    dtype = model.dtypes.get(key)
    if dtype is None:
        return None
    curves = [dtype.ycomp(name) for name in (VOLUME_TOTAL, VOLUME_CONIFER, HEIGHT)]
    if curves[0] is None:
        return None
    payload = tuple(
        None if curve is None else tuple((x, round(y, 6)) for x, y in curve.points())
        for curve in curves
    )
    return "tbl_" + hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()[:12]


def curve_values(model: ForestModel, key: tuple[str, ...]) -> dict[int, tuple[float, float, float, float]]:
    dtype = model.dtypes[key]
    curves = {name: dtype.ycomp(name) for name in (VOLUME_TOTAL, VOLUME_CONIFER, HEIGHT)}
    if curves[VOLUME_TOTAL] is None:
        raise ValueError("development type %s has no %s curve" % (key, VOLUME_TOTAL))
    rows = {}
    for age in AGE_GRID:
        total = float(curves[VOLUME_TOTAL][age])
        conifer = float(curves[VOLUME_CONIFER][age]) if curves[VOLUME_CONIFER] else 0.0
        height = float(curves[HEIGHT][age]) if curves[HEIGHT] else 0.0
        rows[age] = (conifer, max(total - conifer, 0.0), total, height)
    return rows


def curve_is_unimodal(values: list[float], tolerance: float = 1e-6) -> bool:
    if not values:
        return True
    peak = max(range(len(values)), key=lambda index: values[index])
    return all(b <= a + tolerance for a, b in zip(values[peak:], values[peak + 1:]))


def classify_non_forest(key: tuple[str, ...], descriptions: dict[str, str]) -> bool:
    if not key:
        return False
    description = descriptions.get(key[0], "")
    if any(marker in description for marker in NON_FOREST_MARKERS):
        return True
    if len(key) > 1 and key[1] in ("na", "non admissible"):
        return True
    return False


def scan_notices(bundle_dir: Path) -> dict:
    sources = []
    kinds = []
    for path in bundle_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if "lisez_moi" not in name and "confident" not in name:
            continue
        relative = str(path.relative_to(bundle_dir))
        kind = "unknown"
        if path.suffix.lower() == ".txt":
            text = read_model_text(path).lower()
            kind = "confidentiality" if any(marker in text for marker in CONFIDENTIAL_MARKERS) else "modification"
        elif "confident" in name:
            kind = "confidentiality"
        sources.append(relative)
        kinds.append(kind)
    return {
        "notice_present": bool(sources),
        "restricted": "confidentiality" in kinds,
        "notice_kind": "confidentiality" if "confidentiality" in kinds else (kinds[0] if kinds else None),
        "notice_sources": sources,
    }


def write_csv_header(path: Path, columns: tuple[str, ...]) -> tuple[object, csv.DictWriter]:
    handle = open(path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    return handle, writer


def parse_unit(spec: dict, bundles_root: Path, units_out_root: Path) -> dict:
    """Parse one extracted unit into per-unit transactional CSV/JSON outputs."""
    unit = spec["unit"]
    started = utc_now()
    bundle_dir = bundles_root / unit
    marker_path = bundle_dir / ".complete.json"
    if not marker_path.exists():
        raise FileNotFoundError("bundle marker missing for %s" % unit)
    marker = json.loads(marker_path.read_text())
    model_root = bundle_dir / marker["model_root"]
    lan_files = sorted(model_root.glob("*.lan"))
    if len(lan_files) != 1:
        raise ValueError("expected one .lan in %s, found %d" % (model_root, len(lan_files)))
    lan_path = lan_files[0]
    model_name = lan_path.stem

    inventories = locate_inventories(find_carte_dir(bundle_dir))
    crs_labels = []
    crs_warnings = []
    crs_is_projected = True
    for _shp, _dbf, prj_path in inventories:
        label, projected, warning = crs_label(prj_path)
        crs_labels.append(label)
        crs_is_projected = crs_is_projected and projected
        if warning:
            crs_warnings.append(warning)
    crs = crs_labels[0]
    theme_labels, descriptions = parse_theme_info(lan_path)

    warnings: list[str] = list(crs_warnings)
    if len(set(crs_labels)) > 1:
        warnings.append("inventory shapefiles have mixed CRS labels: %s" % sorted(set(crs_labels)))
    if not crs_is_projected:
        warnings.append("inventory CRS is not projected; SUPERFICIE treated as hectares")

    model = ForestModel(
        model_name, str(model_root), base_year=2014, horizon=10, period_length=5
    )
    model.import_landscape_section()
    if model.import_areas_section() != 0:
        raise ValueError("AREAS import failed for %s" % unit)
    model.import_constants_section(on_invalid="skip")
    model.import_yields_section()
    warnings.extend(model.import_warnings)

    model_area_ha = 0.0
    for dtype in model.dtypes.values():
        areas = getattr(dtype, "_areas", None)
        if not areas:
            continue
        model_area_ha += sum(areas.get(0, {}).values())

    temporary = units_out_root / (".tmp-%s-%s" % (unit, uuid.uuid4().hex))
    temporary.mkdir(parents=True)
    try:
        crosswalk_handle, crosswalk_writer = write_csv_header(
            temporary / "crosswalk.csv", CROSSWALK_COLUMNS
        )
        try:
            aggregated: dict[tuple[str, ...], dict] = {}
            polygon_count = 0
            inventory_area_ha = 0.0
            next_polygon_id = 1
            for shp_path, dbf_path, _prj_path in inventories:
                part, part_count, part_area = read_inventory(
                    shp_path,
                    dbf_path,
                    unit,
                    crs,
                    crosswalk_writer,
                    polygon_start=next_polygon_id,
                )
                for key, values in part.items():
                    aggregate = aggregated.get(key)
                    if aggregate is None:
                        aggregate = {"polygon_count": 0, "area_ha": 0.0, "strata": set()}
                        aggregated[key] = aggregate
                    aggregate["polygon_count"] += values["polygon_count"]
                    aggregate["area_ha"] += values["area_ha"]
                    aggregate["strata"].update(values["strata"])
                polygon_count += part_count
                inventory_area_ha += part_area
                next_polygon_id += part_count
        finally:
            crosswalk_handle.close()

        model_keys = set(model.dtypes)
        inventory_keys = set(aggregated)
        model_area_by_key = {}
        for key, dtype in model.dtypes.items():
            areas = getattr(dtype, "_areas", None)
            model_area_by_key[key] = sum(areas.get(0, {}).values()) if areas else 0.0
        model_constant_na = {
            index for index in range(model.nthemes())
            if all(key[index] == "na" for key in model_keys)
        }
        inventory_constant = {
            index for index in range(model.nthemes())
            if len({key[index] for key in inventory_keys}) == 1
        }
        wildcard_themes = tuple(sorted(model_constant_na | inventory_constant))

        def canonical(key: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(
                "?" if index in wildcard_themes else value
                for index, value in enumerate(key)
            )

        model_lookup: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
        for key in sorted(model_keys):
            model_lookup.setdefault(canonical(key), []).append(key)
        if model_constant_na:
            warnings.append(
                "model themes with no non-NA values are treated as wildcards for "
                "inventory matching: %s"
                % ", ".join(str(index + 1) for index in sorted(model_constant_na))
            )
        if inventory_constant - model_constant_na:
            warnings.append(
                "inventory themes with a single value are treated as wildcards for "
                "model matching: %s"
                % ", ".join(str(index + 1) for index in sorted(inventory_constant - model_constant_na))
            )

        table_rows: dict[str, dict[int, tuple[float, float, float, float]]] = {}
        dt_records: list[dict] = []
        zero_volume_examples: list[dict] = []
        ambiguous_examples: list[dict] = []
        ambiguous_dt_count = 0
        ambiguous_area_ha = 0.0

        for key in sorted(model_keys | inventory_keys):
            aggregate = aggregated.get(key)
            area_ha = 0.0 if aggregate is None else aggregate["area_ha"]
            polygon_n = 0 if aggregate is None else aggregate["polygon_count"]
            strata = [] if aggregate is None else sorted(aggregate["strata"])
            match_ambiguous = False
            if key in model_keys:
                matched_key = key
            else:
                candidates = model_lookup.get(canonical(key), [])
                matched_key = None
                if candidates:
                    table_ids = {
                        curve_signature(model, candidate) for candidate in candidates
                    }
                    if len(table_ids) == 1:
                        matched_key = candidates[0]
                    else:
                        match_ambiguous = True
                        matched_key = max(
                            candidates, key=lambda candidate: model_area_by_key[candidate]
                        )
                        ambiguous_dt_count += 1
                        ambiguous_area_ha += area_ha
                        if len(ambiguous_examples) < 10:
                            ambiguous_examples.append(
                                {
                                    "theme_codes": "|".join(key),
                                    "area_ha": round(area_ha, 4),
                                    "candidate_count": len(candidates),
                                    "candidate_tables": len(table_ids),
                                }
                            )
            in_model = matched_key is not None
            table_id = (
                curve_signature(model, matched_key) if in_model and area_ha > 0 else None
            )
            values = None
            if table_id is not None:
                if table_id not in table_rows:
                    table_rows[table_id] = curve_values(model, matched_key)
                values = table_rows[table_id]
            productive = bool(values and max(row[2] for row in values.values()) > 0.0)
            non_forest = classify_non_forest(key, descriptions)
            if not productive and area_ha >= 1.0 and len(zero_volume_examples) < 20:
                zero_volume_examples.append(
                    {
                        "theme_codes": "|".join(key),
                        "area_ha": round(area_ha, 4),
                        "non_forest": non_forest,
                        "in_model": in_model,
                    }
                )
            theme_codes = "|".join(key)
            stratum_labels = "; ".join(strata[:20])
            if len(strata) > 20:
                stratum_labels += "; (+%d more)" % (len(strata) - 20)
            dt_records.append(
                {
                    "unit": unit,
                    "dt_key": "dt_" + hashlib.sha1(theme_codes.encode("utf-8")).hexdigest()[:10],
                    "theme_codes": theme_codes,
                    "polygon_count": polygon_n,
                    "inventory_area_ha": round(area_ha, 4),
                    "stratum_labels": stratum_labels,
                    "table_id": table_id or "",
                    "in_model": int(in_model),
                    "productive": int(productive),
                    "non_forest": int(non_forest),
                    "match_ambiguous": int(match_ambiguous),
                }
            )

        curve_path = temporary / "curves.csv"
        dt_path = temporary / "development_types.csv"
        curve_handle, curve_writer = write_csv_header(curve_path, CURVE_COLUMNS)
        dt_handle, dt_writer = write_csv_header(dt_path, DT_COLUMNS)
        try:
            curve_count = 0
            for table_id in sorted(table_rows):
                for age in AGE_GRID:
                    conifer, deciduous, total, height = table_rows[table_id][age]
                    curve_writer.writerow(
                        {
                            "unit": unit,
                            "development_type": "*",
                            "table_id": table_id,
                            "species_group": "ALL",
                            "age": age,
                            "volume_conifer": round(conifer, 4),
                            "volume_deciduous": round(deciduous, 4),
                            "volume_total": round(total, 4),
                            "height": round(height, 4),
                        }
                    )
                    curve_count += 1
            for record in dt_records:
                dt_writer.writerow(record)
        finally:
            curve_handle.close()
            dt_handle.close()

        productive_area = sum(row["inventory_area_ha"] for row in dt_records if row["productive"])
        covered_area = sum(
            row["inventory_area_ha"]
            for row in dt_records
            if row["in_model"] and row["table_id"]
        )
        covered_productive_area = sum(
            row["inventory_area_ha"]
            for row in dt_records
            if row["in_model"] and row["table_id"] and row["productive"]
        )
        zero_volume_area = sum(
            row["inventory_area_ha"]
            for row in dt_records
            if row["inventory_area_ha"] >= 1.0 and not row["productive"]
        )
        unexplained_zero_volume_area = sum(
            row["inventory_area_ha"]
            for row in dt_records
            if row["inventory_area_ha"] >= 1.0
            and not row["productive"]
            and not row["non_forest"]
        )
        monotonicity_violations = [
            table_id
            for table_id, values in table_rows.items()
            if not curve_is_unimodal([values[age][2] for age in AGE_GRID])
        ]
        monotonicity_max_rise = 0.0
        for values in table_rows.values():
            series = [values[age][2] for age in AGE_GRID]
            peak = max(range(len(series)), key=lambda index: series[index])
            monotonicity_max_rise = max(
                monotonicity_max_rise,
                max((b - a for a, b in zip(series[peak:], series[peak + 1:])), default=0.0),
            )
        negative_tables = [
            table_id
            for table_id, values in table_rows.items()
            if any(min(value[:3]) < -1e-6 for value in values.values())
        ]
        max_split_error = max(
            (
                abs(conifer + deciduous - total)
                for values in table_rows.values()
                for conifer, deciduous, total, _ in values.values()
            ),
            default=0.0,
        )
        missing_conifer_tables = [
            table_id
            for table_id, values in table_rows.items()
            if all(row[0] == 0.0 and row[2] > 0.0 for row in values.values())
        ]
        if missing_conifer_tables:
            warnings.append(
                "%d yield table(s) have no %s conifer curve; volume_conifer set to 0"
                % (len(missing_conifer_tables), VOLUME_CONIFER)
            )
        notices = scan_notices(bundle_dir)
        notice_present = bool(spec.get("notice_present")) or notices["notice_present"]
        notice_kind = spec.get("notice_kind")
        if notices["notice_kind"] == "confidentiality":
            notice_kind = "confidentiality"
        elif notices["notice_kind"] and not notice_kind:
            notice_kind = notices["notice_kind"]
        restricted = bool(spec.get("restricted")) or notices["restricted"] or notice_kind == "confidentiality"
        if restricted:
            warnings.append(
                "confidentiality notice present; inventory-derived tables are restricted"
            )
        if ambiguous_dt_count:
            warnings.append(
                "%d inventory development types covering %.4f ha matched multiple model "
                "tables; the largest model area was used and flagged"
                % (ambiguous_dt_count, ambiguous_area_ha)
            )

        finished = utc_now()
        summary = {
            "unit": unit,
            "region": spec["region"],
            "url": spec["url"],
            "file": spec["file"],
            "listed_bytes": spec["bytes"],
            "model_source": spec.get("model_source"),
            "model_name": model_name,
            "crs": crs,
            "crs_is_projected": crs_is_projected,
            "notice_present": notice_present,
            "notice_kind": notice_kind,
            "notice_url": spec.get("notice_url"),
            "notice_sha256": spec.get("notice_sha256"),
            "notice_sources": sorted(set(spec.get("notice_sources", [])) | set(notices["notice_sources"])),
            "restricted": restricted,
            "runtime_s": round((finished - started).total_seconds(), 3),
            "counts": {
                "inventory_polygons": polygon_count,
                "inventory_dts": len(inventory_keys),
                "model_dts": len(model_keys),
                "yield_tables": len(table_rows),
                "curve_rows": curve_count,
                "dt_rows": len(dt_records),
                "crosswalk_rows": polygon_count,
                "themes": model.nthemes(),
            },
            "areas_ha": {
                "inventory": round(inventory_area_ha, 4),
                "model": round(model_area_ha, 4),
                "relative_difference": round(
                    abs(inventory_area_ha - model_area_ha) / model_area_ha, 8
                ) if model_area_ha else None,
                "productive": round(productive_area, 4),
                "covered_by_table": round(covered_area, 4),
                "covered_by_productive_table": round(covered_productive_area, 4),
                "zero_volume_dt_area": round(zero_volume_area, 4),
                "unexplained_zero_volume_dt_area": round(unexplained_zero_volume_area, 4),
                "ambiguous_match_dt_area": round(ambiguous_area_ha, 4),
            },
            "dt_coverage_fraction": round(covered_area / inventory_area_ha, 6) if inventory_area_ha else None,
            "productive_coverage_fraction": round(
                covered_productive_area / inventory_area_ha, 6
            ) if inventory_area_ha else None,
            "validation": {
                "split_error_max_m3ha": round(max_split_error, 8),
                "monotonicity_violation_count": len(monotonicity_violations),
                "monotonicity_max_rise_m3ha": round(monotonicity_max_rise, 6),
                "monotonicity_violations": monotonicity_violations,
                "negative_volume_tables": negative_tables,
                "zero_volume_examples": zero_volume_examples,
                "ambiguous_match_dt_count": ambiguous_dt_count,
                "ambiguous_match_examples": ambiguous_examples,
                "wildcard_themes": [index + 1 for index in wildcard_themes],
            },
            "theme_labels": theme_labels,
            "warnings": sorted(set(warnings)),
        }
        (temporary / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        unit_dir = units_out_root / unit
        if unit_dir.exists():
            shutil.rmtree(unit_dir)
        os.replace(temporary, unit_dir)
        return summary
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def parse_unit_worker(spec: dict, bundles_root: str, units_out_root: str) -> dict:
    try:
        summary = parse_unit(spec, Path(bundles_root), Path(units_out_root))
        return {"status": "ok", "summary": summary}
    except Exception as exc:
        return {
            "status": "failed",
            "unit": spec["unit"],
            "error": "%s: %s" % (type(exc).__name__, exc),
            "trace": "".join(traceback.format_exc(limit=6).splitlines(keepends=True)[-8:]),
        }


def copy_rows(source: Path, writer: csv.DictWriter) -> int:
    count = 0
    with open(source, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            writer.writerow(row)
            count += 1
    return count


def build_manifest(
    index: dict,
    summaries: list[dict],
    failures: list[dict],
    download_results: dict[str, dict],
    extract_results: dict[str, dict],
    artifact_info: dict,
    started: datetime,
    finished: datetime,
) -> dict:
    totals = {
        "units_with_public_model": len(index["units"]),
        "units_parsed": len(summaries),
        "units_failed": len(failures),
        "units_skipped_no_model": len(index["skipped_units"]),
        "inventory_polygons": sum(item["counts"]["inventory_polygons"] for item in summaries),
        "inventory_dts": sum(item["counts"]["inventory_dts"] for item in summaries),
        "model_dts": sum(item["counts"]["model_dts"] for item in summaries),
        "yield_tables": sum(item["counts"]["yield_tables"] for item in summaries),
        "curve_rows": sum(item["counts"]["curve_rows"] for item in summaries),
        "dt_rows": sum(item["counts"]["dt_rows"] for item in summaries),
        "crosswalk_rows": sum(item["counts"]["crosswalk_rows"] for item in summaries),
        "inventory_area_ha": round(sum(item["areas_ha"]["inventory"] for item in summaries), 4),
        "model_area_ha": round(sum(item["areas_ha"]["model"] for item in summaries), 4),
        "restricted_units": sum(1 for item in summaries if item["restricted"]),
        "notice_units": sum(1 for item in summaries if item["notice_present"]),
        "download_bytes": sum(item.get("bytes", 0) for item in download_results.values()),
        "extracted_bytes": sum(item.get("bytes", 0) for item in extract_results.values()),
    }
    area_mismatches = [
        {
            "unit": item["unit"],
            "inventory_ha": item["areas_ha"]["inventory"],
            "model_ha": item["areas_ha"]["model"],
            "relative_difference": item["areas_ha"]["relative_difference"],
        }
        for item in summaries
        if (item["areas_ha"]["relative_difference"] or 0.0) > 1e-6
    ]
    low_coverage = [
        {
            "unit": item["unit"],
            "dt_coverage_fraction": item["dt_coverage_fraction"],
            "productive_coverage_fraction": item["productive_coverage_fraction"],
        }
        for item in summaries
        if (item["dt_coverage_fraction"] or 0.0) < 1.0
    ]
    zero_volume_suspects = [
        {
            "unit": item["unit"],
            "unexplained_zero_volume_dt_area": item["areas_ha"]["unexplained_zero_volume_dt_area"],
            "examples": item["validation"]["zero_volume_examples"],
        }
        for item in summaries
        if item["areas_ha"]["unexplained_zero_volume_dt_area"] > 0.0
    ]
    ambiguous_matches = [
        {
            "unit": item["unit"],
            "dt_count": item["validation"]["ambiguous_match_dt_count"],
            "area_ha": item["areas_ha"]["ambiguous_match_dt_area"],
            "examples": item["validation"]["ambiguous_match_examples"],
        }
        for item in summaries
        if item["validation"]["ambiguous_match_dt_count"]
    ]
    monotonicity_units = [
        {
            "unit": item["unit"],
            "count": item["validation"]["monotonicity_violation_count"],
            "max_rise_m3ha": item["validation"]["monotonicity_max_rise_m3ha"],
        }
        for item in summaries
        if item["validation"]["monotonicity_violation_count"]
    ]
    return {
        "generated_at": finished.isoformat(),
        "index_url": index["index_url"],
        "source_index_generated_at": index.get("generated_at"),
        "parser": ws3_provenance(),
        "runtime_s": round((finished - started).total_seconds(), 3),
        "storage": {
            "raw_and_extracted": "external scratch (not committed)",
            "public_derivatives": "data/derived/national/qc_bfec_full",
        },
        "counts": totals,
        "units": sorted(summaries, key=lambda item: item["unit"]),
        "failures": failures,
        "skipped_units": index["skipped_units"],
        "validation": {
            "area_mismatch_count": len(area_mismatches),
            "area_mismatches": area_mismatches,
            "dt_coverage_below_one_count": len(low_coverage),
            "dt_coverage_below_one": low_coverage,
            "zero_volume_unexplained_count": len(zero_volume_suspects),
            "zero_volume_unexplained": zero_volume_suspects,
            "ambiguous_match_unit_count": len(ambiguous_matches),
            "ambiguous_match_units": ambiguous_matches,
            "monotonicity_unit_count": len(monotonicity_units),
            "monotonicity_units": monotonicity_units,
            "max_split_error_m3ha": max(
                (item["validation"]["split_error_max_m3ha"] for item in summaries), default=0.0
            ),
            "negative_volume_table_count": sum(
                len(item["validation"]["negative_volume_tables"]) for item in summaries
            ),
        },
        "artifacts": artifact_info,
        "policy": {
            "raw_bundles_published": False,
            "restricted_crosswalk_published": False,
            "notice_units": [item["unit"] for item in summaries if item["notice_present"]],
            "restricted_units": [item["unit"] for item in summaries if item["restricted"]],
            "notice_provenance": "Unit-page and bundle Lisez_moi notices are recorded in each unit summary.",
        },
        "notes": [
            "development_type='*' marks table-level curves; table_id links curves to DT rows.",
            "volume_deciduous is derived as max(volume_total - volume_conifer, 0).",
            "Ages are sampled every 5 years from 0 to 200; source curves are piecewise linear.",
            "Inventory polygon_id is the 1-based shapefile record order; no geometry is written.",
            "Inventory-derived tables for confidentiality-notice units are restricted, not published.",
        ],
    }


def render_report(manifest: dict, index: dict, runtime_s: float) -> str:
    counts = manifest["counts"]
    validation = manifest["validation"]
    lines = [
        "# QC BFEC/Woodstock Full Extraction Report",
        "",
        "**Date:** %s  " % manifest["generated_at"][:10],
        "**Source index:** `%s`  " % manifest["index_url"],
        "**Parser:** ws3 `%s` at `%s`  " % (
            manifest["parser"].get("ws3_version"),
            manifest["parser"].get("ws3_git_commit"),
        ),
        "**Scope:** all public BFEC model bundles; 52 with `Modeles_compresses/*.zip` "
        "plus notice-bearing units `08462` and `08562`, whose bundles are at the unit root. "
        "Thirteen confidential cartography-only units were not downloaded.",
        "",
        "## 1. Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        "| Public model units | %d |" % counts["units_with_public_model"],
        "| Units parsed | %d |" % counts["units_parsed"],
        "| Units failed | %d |" % counts["units_failed"],
        "| No-model units skipped | %d |" % counts["units_skipped_no_model"],
        "| Inventory polygons | %s |" % f"{counts['inventory_polygons']:,}",
        "| Inventory development types | %s |" % f"{counts['inventory_dts']:,}",
        "| Model development types | %s |" % f"{counts['model_dts']:,}",
        "| Unique yield tables | %s |" % f"{counts['yield_tables']:,}",
        "| Curve rows | %s |" % f"{counts['curve_rows']:,}",
        "| DT rows | %s |" % f"{counts['dt_rows']:,}",
        "| Crosswalk rows | %s |" % f"{counts['crosswalk_rows']:,}",
        "| Inventory area (ha) | %s |" % f"{counts['inventory_area_ha']:,}",
        "| Model `.are` area (ha) | %s |" % f"{counts['model_area_ha']:,}",
        "| Restricted units | %d |" % counts["restricted_units"],
        "| Notice-bearing units | %d |" % counts["notice_units"],
        "| Download size | %s |" % bytes_to_human(counts["download_bytes"]),
        "| Extracted size | %s |" % bytes_to_human(counts["extracted_bytes"]),
        "| Wall runtime | %.1f min |" % (runtime_s / 60.0),
        "",
        "## 2. Parser hardening",
        "",
        "The sibling ws3 parser was extended before the full run:",
        "",
        "- `.con` values accept comma decimal separators and, when requested with "
        "`on_invalid='skip'`, text-valued partial declarations are skipped with a warning.",
        "- `#CONSTANT` references resolve in tabular and row-based yield cells.",
        "- Undefined theme mask codes are explicit skips with per-unit warnings instead of "
        "`KeyError`; over-long masks truncate trailing comment tokens with a warning.",
        "- Unsupported `FOREACH` yield macro blocks are skipped with a warning.",
        "- The five-unit sample (`03451`, `02651`, `03152`, `03551`, `03151`) and the three "
        "previous failures (`05151`, `06151`, `09551`) were re-parsed successfully.",
        "",
        "## 3. Validation",
        "",
        "| Check | Result |",
        "|---|---|",
        "| Area mismatches > 1e-6 | %d |" % validation["area_mismatch_count"],
        "| Units with DT coverage < 100%% | %d |" % validation["dt_coverage_below_one_count"],
        "| Max curve split error (m3/ha) | %.8f |" % validation["max_split_error_m3ha"],
        "| Negative-volume tables | %d |" % validation["negative_volume_table_count"],
        "| Units with post-peak monotonicity flags | %d |" % validation["monotonicity_unit_count"],
        "| Zero-volume area needing review (ha) | %.1f |" % sum(
            item["unexplained_zero_volume_dt_area"]
            for item in manifest["validation"]["zero_volume_unexplained"]
        ),
        "| Units with ambiguous model matches | %d |" % validation["ambiguous_match_unit_count"],
        "",
    ]
    if validation["area_mismatches"]:
        lines += [
            "### Area mismatches",
            "",
            "| Unit | Inventory ha | Model ha | Relative diff |",
            "|---|---:|---:|---:|",
        ]
        for item in validation["area_mismatches"][:20]:
            lines.append(
                "| %s | %.1f | %.1f | %.8f |"
                % (item["unit"], item["inventory_ha"], item["model_ha"], item["relative_difference"])
            )
        lines.append("")
    if validation["dt_coverage_below_one"]:
        lines += [
            "### DT coverage below 100%",
            "",
            "| Unit | DT coverage | Productive coverage |",
            "|---|---:|---:|",
        ]
        for item in validation["dt_coverage_below_one"][:20]:
            lines.append(
                "| %s | %.2f%% | %.2f%% |"
                % (
                    item["unit"],
                    100.0 * (item["dt_coverage_fraction"] or 0.0),
                    100.0 * (item["productive_coverage_fraction"] or 0.0),
                )
            )
        lines.append("")
    if validation["monotonicity_units"]:
        lines += [
            "### Post-peak monotonicity flags",
            "",
            "| Unit | Tables | Max rise (m3/ha over 5 yr) |",
            "|---|---:|---:|",
        ]
        for item in validation["monotonicity_units"][:30]:
            lines.append(
                "| %s | %d | %.3f |"
                % (item["unit"], item["count"], item["max_rise_m3ha"])
            )
        lines.append("")
    if validation["zero_volume_unexplained"]:
        lines += [
            "### Zero-volume area needing review",
            "",
            "| Unit | Area ha | Example theme codes |",
            "|---|---:|---|",
        ]
        for item in validation["zero_volume_unexplained"][:20]:
            examples = "; ".join(
                example["theme_codes"] for example in item["examples"][:2]
            )
            lines.append(
                "| %s | %.1f | `%s` |"
                % (item["unit"], item["unexplained_zero_volume_dt_area"], examples)
            )
        lines.append("")
    if validation.get("ambiguous_match_units"):
        lines += [
            "### Ambiguous model matches",
            "",
            "| Unit | DT count | Area ha | Note |",
            "|---|---:|---:|---|",
        ]
        for item in validation["ambiguous_match_units"][:20]:
            lines.append(
                "| %s | %d | %.1f | dominant model area used |"
                % (item["unit"], item["dt_count"], item["area_ha"])
            )
        lines.append("")

    lines += [
        "## 4. Units",
        "",
        "| Unit | Region | Bundles | Polygons | Inventory DT | Tables | Inventory ha | DT cover | Runtime s | Notice |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in manifest["units"]:
        notice = ""
        if item["restricted"]:
            notice = "confidential"
        elif item["notice_present"]:
            notice = "modification"
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %.1f%% | %.1f | %s |"
            % (
                item["unit"],
                item["region"],
                item["model_source"],
                f"{item['counts']['inventory_polygons']:,}",
                f"{item['counts']['inventory_dts']:,}",
                f"{item['counts']['yield_tables']:,}",
                f"{item['areas_ha']['inventory']:,}",
                100.0 * (item["dt_coverage_fraction"] or 0.0),
                item["runtime_s"],
                notice,
            )
        )
    lines += [
        "",
        "## 5. Failures and notices",
        "",
    ]
    if manifest["failures"]:
        lines += ["| Unit | Stage | Error |", "|---|---|---|"]
        for failure in manifest["failures"]:
            lines.append(
                "| %s | %s | `%s` |"
                % (failure.get("unit"), failure.get("stage"), failure.get("error"))
            )
    else:
        lines.append("No unit failed.")
    lines.append("")
    notice_units = [
        item for item in manifest["units"] if item["notice_present"]
    ]
    if notice_units:
        lines += [
            "Notice-bearing units:",
            "",
            "| Unit | Notice kind | Restricted | Sources |",
            "|---|---|---|---|",
        ]
        for item in notice_units:
            sources = "; ".join(item["notice_sources"])
            if item.get("notice_url"):
                sources = (sources + "; " if sources else "") + "unit page: %s" % item["notice_url"]
            lines.append(
                "| %s | %s | %s | %s |"
                % (
                    item["unit"],
                    item["notice_kind"],
                    "yes" if item["restricted"] else "no",
                    sources or "unit page",
                )
            )
        lines.append("")
    lines += [
        "The 13 confidential cartography-only units were not downloaded: "
        + ", ".join(item["unit"] for item in manifest["skipped_units"])
        + ".",
        "",
        "## 6. Artifacts",
        "",
        "| File | Rows | Bytes | SHA-256 |",
        "|---|---:|---:|---|",
    ]
    for name, info in manifest["artifacts"]["public"].items():
        lines.append(
            "| `%s` | %s | %s | `%s` |"
            % (name, f"{info['rows']:,}", f"{info['bytes']:,}", info["sha256"])
        )
    for name, info in manifest["artifacts"]["restricted"].items():
        lines.append(
            "| `restricted/%s` (not published) | %s | %s | `%s` |"
            % (name, f"{info['rows']:,}", f"{info['bytes']:,}", info["sha256"])
        )
    lines += [
        "",
        "Raw bundles and extracted model directories remain in external scratch and "
        "are not committed. The restricted crosswalk is not committed or pushed.",
        "",
        "## 7. Next",
        "",
        "- Cluster and compress the unique yield tables for integration with the CLEWs "
        "forest-supply chain.",
        "- Join development-type mappings to inventory polygons through the restricted "
        "crosswalk inside the secure data environment.",
        "- Validate the curve tables against the earlier five-unit sample and against "
        "provincial yield-curve sources before calibration.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--downloads-root", required=True, type=Path)
    parser.add_argument("--bundles-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--download-workers", type=int, default=4)
    parser.add_argument("--units", nargs="*", help="optional unit subset for a retry")
    parser.add_argument("--expected-count", type=int, default=54)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-extract", action="store_true")
    args = parser.parse_args()

    started = utc_now()
    index = json.loads(args.index.read_text())
    specs = index["units"]
    if args.units:
        wanted = set(args.units)
        specs = [spec for spec in specs if spec["unit"] in wanted]
        missing = wanted - {spec["unit"] for spec in specs}
        if missing:
            raise SystemExit("unknown units: %s" % sorted(missing))
    elif args.expected_count and len(specs) != args.expected_count:
        raise SystemExit(
            "expected %d public model units, index has %d" % (args.expected_count, len(specs))
        )

    args.downloads_root.mkdir(parents=True, exist_ok=True)
    args.bundles_root.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    units_out_root = args.out_dir / "units"
    units_out_root.mkdir(parents=True, exist_ok=True)

    download_results: dict[str, dict] = {}
    failures: list[dict] = []
    if not args.skip_download:
        print("downloading %d bundles with %d workers" % (len(specs), args.download_workers))
        with ThreadPoolExecutor(max_workers=args.download_workers) as pool:
            futures = {
                pool.submit(download_unit, spec, args.downloads_root): spec for spec in specs
            }
            for future in as_completed(futures):
                spec = futures[future]
                try:
                    result = future.result()
                    download_results[spec["unit"]] = result
                    print("  %s %s %.1f MiB" % (
                        spec["unit"], result["status"], result["bytes"] / (1 << 20)))
                except Exception as exc:
                    failures.append(
                        {
                            "unit": spec["unit"],
                            "stage": "download",
                            "url": spec["url"],
                            "error": "%s: %s" % (type(exc).__name__, exc),
                        }
                    )
                    print("  %s FAILED %s" % (spec["unit"], exc))
    else:
        for spec in specs:
            path = args.downloads_root / spec["file"]
            if path.exists():
                download_results[spec["unit"]] = {
                    "unit": spec["unit"],
                    "status": "assumed",
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }

    ready_specs = [spec for spec in specs if spec["unit"] in download_results]
    extract_results: dict[str, dict] = {}
    if args.skip_extract:
        for spec in ready_specs:
            marker = args.bundles_root / spec["unit"] / ".complete.json"
            if not marker.exists():
                continue
            payload = json.loads(marker.read_text())
            extract_results[spec["unit"]] = {
                "unit": spec["unit"],
                "status": "assumed",
                "model_root": payload.get("model_root"),
                "bytes": payload.get("extracted_bytes"),
            }
    elif ready_specs:
        print("extracting %d bundles" % len(ready_specs))
        with ThreadPoolExecutor(max_workers=min(args.download_workers, 4)) as pool:
            futures = {
                pool.submit(extract_unit, spec, args.downloads_root, args.bundles_root): spec
                for spec in ready_specs
            }
            for future in as_completed(futures):
                spec = futures[future]
                try:
                    result = future.result()
                    extract_results[spec["unit"]] = result
                    print("  %s %s %.1f MiB" % (
                        spec["unit"], result["status"], (result["bytes"] or 0) / (1 << 20)))
                except Exception as exc:
                    failures.append(
                        {
                            "unit": spec["unit"],
                            "stage": "extract",
                            "url": spec["url"],
                            "error": "%s: %s" % (type(exc).__name__, exc),
                        }
                    )
                    print("  %s FAILED %s" % (spec["unit"], exc))

    parse_specs = [spec for spec in ready_specs if spec["unit"] in extract_results]
    summaries: list[dict] = []
    if parse_specs:
        print("parsing %d units with %d workers" % (len(parse_specs), args.workers))
        context = get_context("fork")
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=context) as pool:
            futures = {
                pool.submit(
                    parse_unit_worker, spec, str(args.bundles_root), str(units_out_root)
                ): spec
                for spec in parse_specs
            }
            for future in as_completed(futures):
                spec = futures[future]
                result = future.result()
                if result["status"] == "ok":
                    summary = result["summary"]
                    for key in ("bytes", "sha256"):
                        if spec["unit"] in download_results:
                            summary[key] = download_results[spec["unit"]].get(key)
                    summaries.append(summary)
                    print("  %s ok %d tables %.1f s" % (
                        spec["unit"],
                        summary["counts"]["yield_tables"],
                        summary["runtime_s"],
                    ))
                else:
                    failures.append(
                        {
                            "unit": spec["unit"],
                            "stage": "parse",
                            "url": spec["url"],
                            "error": result.get("error"),
                        }
                    )
                    print("  %s FAILED %s" % (spec["unit"], result.get("error")))

    restricted_dir = args.out_dir / "restricted"
    restricted_dir.mkdir(parents=True, exist_ok=True)
    public_curve_path = args.out_dir / "qc_bfec_curves.csv"
    public_dt_path = args.out_dir / "qc_bfec_development_types.csv"
    public_crosswalk_path = args.out_dir / "qc_bfec_inventory_crosswalk.csv"
    restricted_dt_path = restricted_dir / "qc_bfec_development_types_restricted.csv"
    restricted_crosswalk_path = restricted_dir / "qc_bfec_inventory_crosswalk_restricted.csv"

    artifact_rows = {
        "public": {},
        "restricted": {},
    }
    with open(public_curve_path, "w", newline="", encoding="utf-8") as curve_handle, \
         open(public_dt_path, "w", newline="", encoding="utf-8") as dt_handle, \
         open(public_crosswalk_path, "w", newline="", encoding="utf-8") as crosswalk_handle, \
         open(restricted_dt_path, "w", newline="", encoding="utf-8") as rdt_handle, \
         open(restricted_crosswalk_path, "w", newline="", encoding="utf-8") as rcross_handle:
        curve_writer = csv.DictWriter(curve_handle, fieldnames=CURVE_COLUMNS, extrasaction="ignore")
        dt_writer = csv.DictWriter(dt_handle, fieldnames=DT_COLUMNS, extrasaction="ignore")
        cross_writer = csv.DictWriter(crosswalk_handle, fieldnames=CROSSWALK_COLUMNS, extrasaction="ignore")
        rdt_writer = csv.DictWriter(rdt_handle, fieldnames=DT_COLUMNS, extrasaction="ignore")
        rcross_writer = csv.DictWriter(rcross_handle, fieldnames=CROSSWALK_COLUMNS, extrasaction="ignore")
        for writer in (curve_writer, dt_writer, cross_writer, rdt_writer, rcross_writer):
            writer.writeheader()
        public_curve_rows = public_dt_rows = public_cross_rows = 0
        restricted_dt_rows = restricted_cross_rows = 0
        for summary in sorted(summaries, key=lambda item: item["unit"]):
            unit = summary["unit"]
            source = units_out_root / unit
            public_curve_rows += copy_rows(source / "curves.csv", curve_writer)
            if summary["restricted"]:
                restricted_dt_rows += copy_rows(source / "development_types.csv", rdt_writer)
                restricted_cross_rows += copy_rows(source / "crosswalk.csv", rcross_writer)
            else:
                public_dt_rows += copy_rows(source / "development_types.csv", dt_writer)
                public_cross_rows += copy_rows(source / "crosswalk.csv", cross_writer)

    for name, path, rows in (
        ("qc_bfec_curves.csv", public_curve_path, public_curve_rows),
        ("qc_bfec_development_types.csv", public_dt_path, public_dt_rows),
        ("qc_bfec_inventory_crosswalk.csv", public_crosswalk_path, public_cross_rows),
    ):
        artifact_rows["public"][name] = {
            "rows": rows,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    for name, path, rows in (
        ("qc_bfec_development_types_restricted.csv", restricted_dt_path, restricted_dt_rows),
        ("qc_bfec_inventory_crosswalk_restricted.csv", restricted_crosswalk_path, restricted_cross_rows),
    ):
        artifact_rows["restricted"][name] = {
            "rows": rows,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    finished = utc_now()
    manifest = build_manifest(
        index, summaries, failures, download_results, extract_results, artifact_rows, started, finished
    )
    manifest_path = args.out_dir / "qc_bfec_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    report = render_report(manifest, index, manifest["runtime_s"])
    report_path = args.out_dir / "QC_BFEC_EXTRACTION_REPORT.md"
    report_path.write_text(report, encoding="utf-8")

    print(json.dumps({
        "counts": manifest["counts"],
        "validation": {
            "area_mismatch_count": manifest["validation"]["area_mismatch_count"],
            "dt_coverage_below_one_count": manifest["validation"]["dt_coverage_below_one_count"],
            "zero_volume_unexplained_count": manifest["validation"]["zero_volume_unexplained_count"],
            "monotonicity_unit_count": manifest["validation"]["monotonicity_unit_count"],
        },
        "failures": [(item["unit"], item["error"]) for item in failures],
        "artifacts": artifact_rows,
    }, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
