#!/usr/bin/env python3
"""Extract a small BFEC/Woodstock sample into normalized derived tables.

The script expects one directory per unit under ``--extract-root`` containing
the unzipped BFEC ``Modeles_compresses`` bundle. Raw bundles stay outside the
repository; this script writes only CSV/JSON text artifacts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import shapefile  # pyshp
from pyproj import CRS

from ws3.forest import ForestModel, read_model_text


VOLUME_CONIFER = "yv_g_gr"
VOLUME_TOTAL = "yv_s"
HEIGHT = "yh_s"
AGE_GRID = tuple(range(0, 201, 5))
TABLE_COLUMNS = (
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
)
CROSSWALK_COLUMNS = (
    ("unit", "polygon_id")
    + tuple("THEME%d" % i for i in range(1, 21))
    + ("STRATE", "area_ha", "CRS")
)


def find_one(directory: Path, suffix: str) -> Path:
    """Return the single case-insensitive file with ``suffix`` in directory."""
    matches = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == suffix.lower()
    )
    if len(matches) != 1:
        raise FileNotFoundError(
            "expected exactly one %s file in %s, found %s"
            % (suffix, directory, [p.name for p in matches])
        )
    return matches[0]


def locate_model(extract_dir: Path) -> tuple[Path, str]:
    lan = find_one(extract_dir, ".lan")
    return lan, lan.stem


def locate_inventory(extract_dir: Path) -> tuple[Path, Path, Path]:
    carte = extract_dir / "Carte"
    if not carte.is_dir():
        raise FileNotFoundError("missing Carte/ directory in %s" % extract_dir)
    return find_one(carte, ".shp"), find_one(carte, ".dbf"), find_one(carte, ".prj")


def parse_theme_names(lan_path: Path) -> list[str]:
    """Extract theme labels from ``*THEME <index> <label>`` declarations."""
    names: list[str] = []
    for line in read_model_text(lan_path).splitlines():
        if not line.upper().startswith("*THEME"):
            continue
        parts = line.split(None, 2)
        names.append(parts[2].strip() if len(parts) > 2 else "theme%d" % (len(names) + 1))
    return names


def crs_label(prj_path: Path) -> str:
    crs = CRS.from_wkt(read_model_text(prj_path))
    epsg = crs.to_epsg()
    return "EPSG:%d" % epsg if epsg else crs.to_string()


def import_model(model_name: str, extract_dir: Path) -> tuple[ForestModel, list[str]]:
    """Import landscape, areas, constants and yields from a BFEC bundle."""
    warnings: list[str] = []
    model = ForestModel(model_name, str(extract_dir), base_year=2014, horizon=10, period_length=5)
    model.import_landscape_section()
    model.import_areas_section()
    try:
        model.import_constants_section()
    except ValueError as exc:
        warnings.append("constants import skipped: %s" % exc)
    model.import_yields_section()
    return model, warnings


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
    """Evaluate conifer, deciduous, total volume and height on the age grid."""
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


def read_inventory(
    shp_path: Path, dbf_path: Path, unit: str, crs: str
) -> tuple[list[dict], dict[tuple[str, ...], dict]]:
    """Read polygon attributes and aggregate them by development type key."""
    reader = shapefile.Reader(shp=str(shp_path), dbf=str(dbf_path), encoding="latin-1")
    fields = [field[0] for field in reader.fields[1:]]
    theme_indexes = [fields.index("THEME%d" % i) for i in range(1, 21)]
    area_index = fields.index("SUPERFICIE")
    strate_index = fields.index("STRATE") if "STRATE" in fields else None

    crosswalk: list[dict] = []
    aggregated: dict[tuple[str, ...], dict] = defaultdict(
        lambda: {"polygon_count": 0, "area_ha": 0.0, "strata": set()}
    )
    for polygon_id, record in enumerate(reader.iterRecords(), start=1):
        themes = [str(record[i]).strip().lower() for i in theme_indexes]
        area_ha = float(record[area_index] or 0.0)
        strate = str(record[strate_index]).strip() if strate_index is not None else ""
        key = tuple(themes)
        aggregate = aggregated[key]
        aggregate["polygon_count"] += 1
        aggregate["area_ha"] += area_ha
        if strate:
            aggregate["strata"].add(strate)
        row = {"unit": unit, "polygon_id": polygon_id, "area_ha": area_ha, "CRS": crs}
        row.update({"THEME%d" % (i + 1): themes[i] for i in range(20)})
        row["STRATE"] = strate
        crosswalk.append(row)
    reader.close()
    return crosswalk, aggregated


def curve_is_unimodal(values: list[float], tolerance: float = 1e-6) -> bool:
    """True when values never rise again after their global maximum."""
    if not values:
        return True
    peak = max(range(len(values)), key=lambda i: values[i])
    return all(b <= a + tolerance for a, b in zip(values[peak:], values[peak + 1:]))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def confidentiality_check(extract_dir: Path, zip_path: Path | None) -> list[str]:
    hits = [
        str(path.relative_to(extract_dir))
        for path in extract_dir.rglob("*")
        if path.is_file() and ("lisez" in path.name.lower() or "confident" in path.name.lower())
    ]
    if zip_path and zip_path.exists():
        with zipfile.ZipFile(zip_path) as archive:
            hits.extend(
                name for name in archive.namelist()
                if "lisez" in name.lower() or "confident" in name.lower()
            )
    return hits


def process_unit(spec: dict, extract_root: Path, downloads_root: Path) -> tuple[dict, list[dict], list[dict], list[dict]]:
    unit = spec["unit"]
    started = datetime.now(timezone.utc)
    extract_dir = extract_root / unit
    lan_path, model_name = locate_model(extract_dir)
    shp_path, dbf_path, prj_path = locate_inventory(extract_dir)
    crs = crs_label(prj_path)
    theme_names = parse_theme_names(lan_path)

    model, warnings = import_model(model_name, extract_dir)
    model_area_ha = sum(
        area for dtype in model.dtypes.values() for area in dtype._areas[0].values()
    )
    crosswalk, aggregated = read_inventory(shp_path, dbf_path, unit, crs)
    inventory_area_ha = sum(row["area_ha"] for row in crosswalk)

    model_keys = set(model.dtypes)
    inventory_keys = set(aggregated)
    table_rows: dict[str, dict[int, tuple[float, float, float, float]]] = {}
    dt_records: list[dict] = []

    all_keys = sorted(model_keys | inventory_keys)
    for key in all_keys:
        aggregate = aggregated.get(key)
        area_ha = 0.0 if aggregate is None else aggregate["area_ha"]
        polygon_count = 0 if aggregate is None else aggregate["polygon_count"]
        strata = [] if aggregate is None else sorted(aggregate["strata"])
        in_model = key in model_keys
        table_id = curve_signature(model, key) if in_model and area_ha > 0 else None
        values = None
        if table_id is not None:
            if table_id not in table_rows:
                table_rows[table_id] = curve_values(model, key)
            values = table_rows[table_id]
        productive = bool(values and max(row[2] for row in values.values()) > 0)
        theme_codes = "|".join(key)
        stratum_labels = "; ".join(strata[:20])
        if len(strata) > 20:
            stratum_labels += "; (+%d more)" % (len(strata) - 20)
        dt_records.append(
            {
                "unit": unit,
                "dt_key": "dt_" + hashlib.sha1(theme_codes.encode("utf-8")).hexdigest()[:10],
                "theme_codes": theme_codes,
                "polygon_count": polygon_count,
                "inventory_area_ha": round(area_ha, 4),
                "stratum_labels": stratum_labels,
                "table_id": table_id or "",
                "in_model": int(in_model),
                "productive": int(productive),
            }
        )

    curve_rows: list[dict] = []
    for table_id in sorted(table_rows):
        for age in AGE_GRID:
            conifer, deciduous, total, height = table_rows[table_id][age]
            curve_rows.append(
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

    productive_area = sum(
        row["inventory_area_ha"] for row in dt_records if row["productive"]
    )
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
    monotonicity_violations = [
        table_id for table_id, values in table_rows.items()
        if not curve_is_unimodal([values[age][2] for age in AGE_GRID])
    ]
    monotonicity_max_rise = 0.0
    for values in table_rows.values():
        series = [values[age][2] for age in AGE_GRID]
        peak = max(range(len(series)), key=lambda i: series[i])
        monotonicity_max_rise = max(
            monotonicity_max_rise,
            max((b - a for a, b in zip(series[peak:], series[peak + 1:])), default=0.0),
        )
    negative_tables = [
        table_id for table_id, values in table_rows.items()
        if any(min(value[:3]) < -1e-6 for value in values.values())
    ]
    max_split_error = max(
        (
            abs(conifer + deciduous - total)
            for values in table_rows.values() for conifer, deciduous, total, _ in values.values()
        ),
        default=0.0,
    )
    zip_path = downloads_root / spec["file"]
    finished = datetime.now(timezone.utc)
    summary = {
        "unit": unit,
        "region": spec["region"],
        "url": spec["url"],
        "file": spec["file"],
        "bytes": spec["bytes"],
        "sha256": spec["sha256"],
        "model_name": model_name,
        "crs": crs,
        "runtime_s": round((finished - started).total_seconds(), 3),
        "counts": {
            "inventory_polygons": len(crosswalk),
            "inventory_dts": len(inventory_keys),
            "model_dts": len(model_keys),
            "yield_tables": len(table_rows),
            "curve_rows": len(curve_rows),
            "dt_rows": len(dt_records),
            "crosswalk_rows": len(crosswalk),
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
        },
        "theme_names": theme_names,
        "confidentiality_hits": confidentiality_check(extract_dir, zip_path),
        "warnings": warnings,
    }
    return summary, curve_rows, dt_records, crosswalk


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", required=True, type=Path, help="unit input JSON")
    parser.add_argument("--extract-root", required=True, type=Path, help="unzipped unit directories")
    parser.add_argument("--downloads-root", required=True, type=Path, help="downloaded zip directory")
    parser.add_argument("--out-dir", required=True, type=Path, help="derived output directory")
    PATH_ARGS = parser.parse_args()

    payload = json.loads(PATH_ARGS.units.read_text())
    PATH_ARGS.out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    unit_summaries = []
    all_curves: list[dict] = []
    all_dts: list[dict] = []
    all_crosswalk: list[dict] = []
    failures = list(payload.get("failed_units", []))

    for spec in payload["units"]:
        try:
            summary, curves, dts, crosswalk = process_unit(
                spec, PATH_ARGS.extract_root, PATH_ARGS.downloads_root
            )
        except Exception as exc:  # fail loudly per unit, continue the batch
            failures.append({**spec, "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        unit_summaries.append(summary)
        all_curves.extend(curves)
        all_dts.extend(dts)
        all_crosswalk.extend(crosswalk)

    curve_path = PATH_ARGS.out_dir / "qc_bfec_curves.csv"
    dt_path = PATH_ARGS.out_dir / "qc_bfec_development_types.csv"
    crosswalk_path = PATH_ARGS.out_dir / "qc_bfec_inventory_crosswalk.csv"
    manifest_path = PATH_ARGS.out_dir / "qc_bfec_sample_manifest.json"
    write_csv(curve_path, TABLE_COLUMNS, all_curves)
    write_csv(dt_path, DT_COLUMNS, all_dts)
    write_csv(crosswalk_path, CROSSWALK_COLUMNS, all_crosswalk)

    finished = datetime.now(timezone.utc)
    manifest = {
        "generated_at": finished.isoformat(),
        "index_url": payload["index_url"],
        "runtime_s": round((finished - started).total_seconds(), 3),
        "units_parsed": len(unit_summaries),
        "units_requested": len(payload["units"]),
        "units": unit_summaries,
        "failed_units": failures,
        "totals": {
            "inventory_polygons": sum(u["counts"]["inventory_polygons"] for u in unit_summaries),
            "inventory_dts": sum(u["counts"]["inventory_dts"] for u in unit_summaries),
            "yield_tables": sum(u["counts"]["yield_tables"] for u in unit_summaries),
            "curve_rows": len(all_curves),
            "dt_rows": len(all_dts),
            "crosswalk_rows": len(all_crosswalk),
            "inventory_area_ha": round(sum(u["areas_ha"]["inventory"] for u in unit_summaries), 4),
            "model_area_ha": round(sum(u["areas_ha"]["model"] for u in unit_summaries), 4),
        },
        "artifacts": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in (curve_path, dt_path, crosswalk_path)
        },
        "confidentiality": {
            "notice_found": any(u["confidentiality_hits"] for u in unit_summaries),
            "units_with_notice": [
                u["unit"] for u in unit_summaries if u["confidentiality_hits"]
            ],
            "policy": "Raw bundles remain in /tmp/bfec_batch; only text derivatives are committed.",
        },
        "notes": [
            "development_type='*' marks table-level curves; table_id links to dt rows.",
            "volume_deciduous is derived as max(volume_total - volume_conifer, 0).",
            "Ages are sampled every 5 years from 0 to 200; source curves are piecewise linear.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["totals"], indent=2))
    print("artifacts:")
    print(json.dumps(manifest["artifacts"], indent=2))
    print("failures:", [(f.get("unit"), f.get("error")) for f in failures])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
