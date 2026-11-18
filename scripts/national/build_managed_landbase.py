"""Build a national managed-landbase *proxy* from compiled tenure and protected areas.

The output is an evidence-tiered screening proxy. It is NOT a legal timber
harvesting landbase (THLB), does not model operability, and must not be used as
an AAC landbase.

Method
------
Tenure polygons (GFW Canada Forest Tenures 2016) and CPCAD protected areas are
rasterized onto a common Canada Albers grid (default 500 m).  Every grid cell is
assigned to a province and an ecodistrict.  SCANFI ecodistrict forest area is
converted to a forest fraction, so tenure and protected-area footprints can be
converted to forest-area equivalents without pretending that tenure polygons are
all productive forest.

Class-level footprints overlap (for example volume-based licences nested inside
area-based licences).  Class rows therefore report gross class footprint, while
``managed_proxy_ha`` is apportioned to the tenure union so that class rows sum to
the union-based proxy and do not double count.

Usage:
    .venv/bin/python scripts/national/build_managed_landbase.py \
        --out-dir data/derived/national/managed_landbase
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio.features
import shapely
from rasterio.transform import from_origin

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TENURE = REPO_ROOT / "data/raw/gfw/tenures/Canada_Forest_Tenures_2016.shp"
DEFAULT_PROVINCE = REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
DEFAULT_CPCAD = REPO_ROOT / "data/raw/cpcad/cpcad_rest_current.gpkg"
DEFAULT_ECOSTRAT = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_AREA_WEIGHTS = (
    REPO_ROOT
    / "data/derived/national/assortments/national_area_weights/national_area_weights_ecodistrict.csv"
)

PROVINCE_CODE = {
    "Newfoundland and Labrador": "NL",
    "Prince Edward Island": "PE",
    "Nova Scotia": "NS",
    "New Brunswick": "NB",
    "Quebec": "QC",
    "Ontario": "ON",
    "Manitoba": "MB",
    "Saskatchewan": "SK",
    "Alberta": "AB",
    "British Columbia": "BC",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}
ALL_PROVINCES = sorted(PROVINCE_CODE.values())

TENURE_SOURCE = (
    "GFW Canada Forest Tenures 2016 (ArcGIS item aa8797d31edd47209e8eabf1c7789e5c)"
)
CPCAD_SOURCE = "CPCAD ESRI REST (Environment and Climate Change Canada), retrieved 2026-11-18"
AREA_SOURCE = "SCANFI v2 national area weights (ecodistrict)"


@dataclass(frozen=True)
class Inputs:
    tenure: Path
    province: Path
    cpcad: Path
    ecostrat: Path
    area_weights: Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repair_geometry(geom):
    if geom is None or geom.is_empty or geom.is_valid:
        return geom
    fixed = shapely.make_valid(geom)
    polygons = [
        part
        for part in shapely.get_parts(fixed)
        if part.geom_type in ("Polygon", "MultiPolygon")
    ]
    return shapely.union_all(polygons) if polygons else geom


def normalise_tenure(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    frame = frame.copy()
    frame["CLASS"] = (
        frame["CLASS"]
        .astype("string")
        .str.replace("permis de rÃ©colte", "permis de récolte", regex=False)
        .fillna("Not specified")
    )
    frame["TYPE"] = frame["TYPE"].astype("string").fillna("Not specified")
    frame["JURISDICTI"] = frame["JURISDICTI"].astype("string")
    frame = frame[frame["JURISDICTI"].isin(PROVINCE_CODE)].copy()
    return frame


def load_inputs(inputs: Inputs) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, pd.Series, int]:
    for path in inputs.__dict__.values():
        if not Path(path).exists():
            raise FileNotFoundError(f"required input missing: {path}")

    tenure = gpd.read_file(inputs.tenure)
    tenure = normalise_tenure(tenure)
    tenure["geometry"] = tenure.geometry.apply(repair_geometry)
    tenure = tenure.set_crs(tenure.crs, allow_override=True)
    tenure = tenure[~tenure.geometry.is_empty].copy()
    tenure["geometry_wkb"] = tenure.geometry.apply(lambda geom: geom.wkb_hex)
    duplicate_count = int(tenure["geometry_wkb"].duplicated().sum())
    tenure = tenure.drop_duplicates("geometry_wkb").drop(columns="geometry_wkb").reset_index(drop=True)

    province = gpd.read_file(inputs.province).to_crs(tenure.crs)
    province["geometry"] = province.geometry.apply(repair_geometry)
    province["province_code"] = province["shapeName"].map(PROVINCE_CODE)
    province = province.dropna(subset=["province_code"]).copy()

    cpcad = gpd.read_file(inputs.cpcad).to_crs(tenure.crs)
    cpcad = cpcad[cpcad.geometry.notna() & ~cpcad.geometry.is_empty].copy()

    ecostrat = gpd.read_file(inputs.ecostrat, layer="ecodistricts").to_crs(tenure.crs)
    ecostrat["ecodistrict_id"] = ecostrat["ecodistrict_id"].astype(int)
    ecostrat = ecostrat[["ecodistrict", "ecoprovince", "ecoprovince_id", "ecodistrict_id", "geometry"]]

    area_weights = pd.read_csv(inputs.area_weights)
    area_weights = area_weights[area_weights["level"] == "ecodistrict"].copy()
    area_weights["ecodistrict_id"] = area_weights["ecodistrict_id"].astype(int)
    area_weights = (
        area_weights.drop_duplicates(["province", "ecodistrict_id"])
        .set_index(["province", "ecodistrict_id"])["forest_area_ha"]
    )
    return tenure, province, cpcad, ecostrat, area_weights, duplicate_count


def raster_counts(values: np.ndarray, mask: np.ndarray, n_keys: int) -> np.ndarray:
    selected = values[mask]
    if selected.size == 0:
        return np.zeros(n_keys, dtype=np.int64)
    return np.bincount(selected, minlength=n_keys).astype(np.int64)


def rasterize_mask(frame: gpd.GeoDataFrame, shape: tuple[int, int], transform) -> np.ndarray:
    return rasterio.features.rasterize(
        ((geom, 1) for geom in frame.geometry if geom is not None and not geom.is_empty),
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    ).astype(bool)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenure", type=Path, default=DEFAULT_TENURE)
    parser.add_argument("--province", type=Path, default=DEFAULT_PROVINCE)
    parser.add_argument("--cpcad", type=Path, default=DEFAULT_CPCAD)
    parser.add_argument("--ecostrat", type=Path, default=DEFAULT_ECOSTRAT)
    parser.add_argument("--area-weights", type=Path, default=DEFAULT_AREA_WEIGHTS)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data/derived/national/managed_landbase")
    parser.add_argument("--resolution", type=int, default=500, help="grid cell size in metres")
    args = parser.parse_args()

    inputs = Inputs(
        tenure=args.tenure,
        province=args.province,
        cpcad=args.cpcad,
        ecostrat=args.ecostrat,
        area_weights=args.area_weights,
    )
    tenure, province, cpcad, ecostrat, area_weights, duplicate_count = load_inputs(inputs)

    min_x, min_y, max_x, max_y = province.total_bounds
    width = int(np.ceil((max_x - min_x) / args.resolution))
    height = int(np.ceil((max_y - min_y) / args.resolution))
    transform = from_origin(min_x, max_y, args.resolution, args.resolution)
    shape = (height, width)
    cell_ha = args.resolution * args.resolution / 10_000.0

    province_codes = sorted(province["province_code"].unique())
    province_index = {code: index + 1 for index, code in enumerate(province_codes)}
    province_raster = rasterio.features.rasterize(
        (
            (geom, province_index[code])
            for geom, code in zip(province.geometry, province["province_code"])
        ),
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int16",
    )

    ecodistricts = ecostrat.drop_duplicates("ecodistrict_id").copy()
    ecodistrict_ids = sorted(ecodistricts["ecodistrict_id"].tolist())
    ecodistrict_index = {ecoid: index + 1 for index, ecoid in enumerate(ecodistrict_ids)}
    ecodistrict_raster = rasterio.features.rasterize(
        (
            (geom, ecodistrict_index[ecoid])
            for geom, ecoid in zip(ecodistricts.geometry, ecodistricts["ecodistrict_id"])
        ),
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int16",
    )

    max_province = max(province_index.values())
    max_ecodistrict = max(ecodistrict_index.values())
    key_raster = province_raster.astype(np.int32) * (max_ecodistrict + 1) + ecodistrict_raster
    n_keys = (max_province + 1) * (max_ecodistrict + 1)

    key_to_province = {}
    key_to_ecodistrict = {}
    for province_code, province_id in province_index.items():
        for ecodistrict_id, eco_index in ecodistrict_index.items():
            key = province_id * (max_ecodistrict + 1) + eco_index
            key_to_province[key] = province_code
            key_to_ecodistrict[key] = ecodistrict_id

    protected_mask = rasterize_mask(cpcad, shape, transform)

    class_column = list(zip(tenure["TYPE"], tenure["CLASS"]))
    class_keys = sorted(set(class_column))
    occupied = np.zeros(shape, dtype=bool)
    class_records = []
    for tenure_type, tenure_class in class_keys:
        subset = tenure[(tenure["TYPE"] == tenure_type) & (tenure["CLASS"] == tenure_class)]
        mask = rasterize_mask(subset, shape, transform)
        occupied |= mask
        class_records.append(
            {
                "tenure_type": tenure_type,
                "tenure_class": tenure_class,
                "footprint_by_key": raster_counts(key_raster, mask, n_keys),
                "protected_by_key": raster_counts(key_raster, mask & protected_mask, n_keys),
            }
        )

    total_by_key = np.bincount(key_raster.ravel(), minlength=n_keys).astype(np.int64)
    union_by_key = raster_counts(key_raster, occupied, n_keys)
    union_protected_by_key = raster_counts(key_raster, occupied & protected_mask, n_keys)

    area_by_key = total_by_key * cell_ha
    forest_by_key = np.zeros(n_keys, dtype=float)
    for (province_code, ecodistrict_id), forest_area_ha in area_weights.items():
        province_id = province_index.get(province_code)
        eco_index = ecodistrict_index.get(int(ecodistrict_id))
        if province_id is None or eco_index is None:
            continue
        key = province_id * (max_ecodistrict + 1) + eco_index
        forest_by_key[key] = float(forest_area_ha)
    with np.errstate(divide="ignore", invalid="ignore"):
        forest_fraction = np.where(area_by_key > 0, forest_by_key / area_by_key, 0.0)
    forest_fraction = np.clip(forest_fraction, 0.0, 1.0)

    union_forest = union_by_key * cell_ha * forest_fraction
    union_protected_forest = union_protected_by_key * cell_ha * forest_fraction
    union_managed = np.clip(union_forest - union_protected_forest, 0.0, None)

    managed_denominator = np.zeros(n_keys, dtype=float)
    for record in class_records:
        gross = record["footprint_by_key"] * cell_ha * forest_fraction
        excluded = record["protected_by_key"] * cell_ha * forest_fraction
        record["forest_ha"] = gross
        record["protected_forest_ha"] = excluded
        record["managed_gross_ha"] = np.clip(gross - excluded, 0.0, None)
        managed_denominator += record["managed_gross_ha"]
    with np.errstate(divide="ignore", invalid="ignore"):
        apportion = np.where(
            managed_denominator > 0, union_managed / managed_denominator, 0.0
        )
    for record in class_records:
        record["managed_proxy_ha"] = record["managed_gross_ha"] * apportion

    ecodistrict_lookup = ecostrat.drop_duplicates("ecodistrict_id").set_index("ecodistrict_id")
    rows = []
    for record in class_records:
        for key in np.flatnonzero(record["footprint_by_key"]):
            province_code = key_to_province.get(int(key))
            if province_code is None:
                continue
            ecodistrict_id = key_to_ecodistrict[key]
            if ecodistrict_id not in ecodistrict_lookup.index:
                continue
            eco_row = ecodistrict_lookup.loc[ecodistrict_id]
            rows.append(
                {
                    "province": province_code,
                    "ecoprovince": eco_row["ecoprovince"],
                    "ecoprovince_id": eco_row["ecoprovince_id"],
                    "ecodistrict": eco_row["ecodistrict"],
                    "ecodistrict_id": ecodistrict_id,
                    "tenure_type": record["tenure_type"],
                    "tenure_class": record["tenure_class"],
                    "tenure_footprint_ha": record["footprint_by_key"][key] * cell_ha,
                    "forest_area_ha": record["forest_ha"][key],
                    "protected_excluded_ha": record["protected_forest_ha"][key],
                    "managed_proxy_ha": record["managed_proxy_ha"][key],
                }
            )
    detail = pd.DataFrame(rows)
    if detail.empty:
        raise RuntimeError("no tenure class overlaps were produced; refusing to write empty outputs")
    detail = detail[detail["tenure_footprint_ha"] > 0].copy()

    province_forest = (
        pd.Series(
            {
                code: sum(
                    float(value)
                    for (province_code, _), value in area_weights.items()
                    if province_code == code
                )
                for code in province_codes
            }
        )
    )
    ecoprovince_forest = (
        ecostrat.drop_duplicates(["ecodistrict_id", "ecoprovince_id"])
        .merge(
            area_weights.rename("forest_area_ha").reset_index(),
            on="ecodistrict_id",
            how="left",
        )
        .groupby(["ecoprovince_id", "ecoprovince"])["forest_area_ha"]
        .sum()
    )

    detail["scanfi_forest_area_ha"] = detail["province"].map(province_forest)
    detail["managed_share"] = detail["managed_proxy_ha"] / detail["scanfi_forest_area_ha"]
    detail["source"] = f"{TENURE_SOURCE}; {CPCAD_SOURCE}; {AREA_SOURCE}"
    detail["evidence_tier"] = "B"
    detail["coverage"] = "tenure_2016"
    detail["notes"] = (
        f"{args.resolution} m grid overlay; class footprint gross (tenures overlap); "
        "managed proxy apportioned to tenure union; managed_share is share of provincial SCANFI forest; protected exclusion all CPCAD polygons"
    )

    province_detail = (
        detail.groupby(
            ["province", "tenure_type", "tenure_class"], as_index=False
        )[
            [
                "tenure_footprint_ha",
                "forest_area_ha",
                "protected_excluded_ha",
                "managed_proxy_ha",
            ]
        ]
        .sum()
    )
    province_detail["scanfi_forest_area_ha"] = province_detail["province"].map(province_forest)
    province_detail["managed_share"] = (
        province_detail["managed_proxy_ha"] / province_detail["scanfi_forest_area_ha"]
    )
    province_detail["source"] = f"{TENURE_SOURCE}; {CPCAD_SOURCE}; {AREA_SOURCE}"
    province_detail["evidence_tier"] = "B"
    province_detail["coverage"] = "tenure_2016"
    province_detail["notes"] = (
        f"{args.resolution} m grid overlay; footprint gross; managed proxy apportioned to union; "
        "managed_share is share of provincial SCANFI forest; 2016 tenure vintage; screening proxy only, not a legal THLB"
    )

    missing = [code for code in ALL_PROVINCES if code not in set(province_detail["province"])]
    missing_rows = pd.DataFrame(
        [
            {
                "province": code,
                "tenure_type": "ALL",
                "tenure_class": "ALL",
                "tenure_footprint_ha": np.nan,
                "forest_area_ha": np.nan,
                "protected_excluded_ha": np.nan,
                "managed_proxy_ha": np.nan,
                "managed_share": np.nan,
                "scanfi_forest_area_ha": float(province_forest.get(code, np.nan)),
                "source": "no accepted tenure source",
                "evidence_tier": "missing",
                "coverage": "missing",
                "notes": "No GFW 2016 tenure polygons for this jurisdiction; value intentionally blank, not zero",
            }
            for code in missing
        ]
    )
    province_out = pd.concat([province_detail, missing_rows], ignore_index=True)
    province_out = province_out.sort_values(
        ["province", "tenure_type", "tenure_class"], na_position="last"
    )

    ecoprovince_detail = (
        detail.groupby(
            ["ecoprovince_id", "ecoprovince", "province", "tenure_type", "tenure_class"],
            as_index=False,
        )[
            [
                "tenure_footprint_ha",
                "forest_area_ha",
                "protected_excluded_ha",
                "managed_proxy_ha",
            ]
        ]
        .sum()
    )
    ecoprovince_detail["scanfi_forest_area_ha"] = (
        ecoprovince_detail.set_index(["ecoprovince_id", "ecoprovince"])
        .index.map(ecoprovince_forest)
        .to_numpy()
    )
    ecoprovince_detail["managed_share"] = (
        ecoprovince_detail["managed_proxy_ha"] / ecoprovince_detail["scanfi_forest_area_ha"]
    )
    ecoprovince_detail["source"] = f"{TENURE_SOURCE}; {CPCAD_SOURCE}; {AREA_SOURCE}"
    ecoprovince_detail["evidence_tier"] = "B"
    ecoprovince_detail["coverage"] = "tenure_2016"
    ecoprovince_detail["notes"] = (
        f"{args.resolution} m grid overlay; apportioned to union; managed_share is share of ecoprovincial SCANFI forest; screening proxy only"
    )
    ecoprovince_out = ecoprovince_detail.sort_values(
        ["ecoprovince_id", "province", "tenure_type", "tenure_class"]
    )

    summary_rows = []
    for code in ALL_PROVINCES:
        province_keys = [key for key, value in key_to_province.items() if value == code]
        if not province_keys:
            continue
        footprint = sum(union_by_key[key] for key in province_keys) * cell_ha
        forest = sum(union_forest[key] for key in province_keys)
        protected_forest = sum(union_protected_forest[key] for key in province_keys)
        managed = sum(union_managed[key] for key in province_keys)
        scanfi = float(province_forest.get(code, np.nan))
        has_tenure = footprint > 0
        summary_rows.append(
            {
                "province": code,
                "tenure_union_footprint_ha": footprint if has_tenure else np.nan,
                "tenure_union_forest_ha": forest if has_tenure else np.nan,
                "protected_excluded_ha": protected_forest if has_tenure else np.nan,
                "managed_proxy_ha": managed if has_tenure else np.nan,
                "managed_share": managed / scanfi if has_tenure and scanfi else np.nan,
                "scanfi_forest_area_ha": scanfi,
                "coverage": "tenure_2016" if has_tenure else "missing",
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values("province")

    covered = summary[summary["coverage"] == "tenure_2016"]
    total_class_managed = province_detail["managed_proxy_ha"].sum()
    total_union_managed = covered["managed_proxy_ha"].sum()
    overlap_ratio = (
        province_detail["forest_area_ha"].sum() / covered["tenure_union_forest_ha"].sum()
    )
    checks = {
        "tenure_input_features_raw": len(tenure) + duplicate_count,
        "tenure_analysis_features": len(tenure),
        "tenure_duplicate_geometries_removed": duplicate_count,
        "tenure_union_forest_le_scanfi_forest": bool(
            (covered["tenure_union_forest_ha"] <= covered["scanfi_forest_area_ha"] + 1).all()
        ),
        "protected_exclusion_le_tenure_forest": bool(
            (covered["protected_excluded_ha"] <= covered["tenure_union_forest_ha"] + 1).all()
        ),
        "class_managed_sums_to_union": bool(
            abs(total_class_managed - total_union_managed) <= max(1.0, total_union_managed * 1e-6)
        ),
        "class_forest_to_union_forest_ratio": float(overlap_ratio),
        "national_managed_proxy_ha": float(total_union_managed),
        "national_scanfi_forest_ha": float(summary["scanfi_forest_area_ha"].sum()),
        "missing_jurisdictions": missing,
        "cpcad_features": len(cpcad),
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    province_out.to_csv(args.out_dir / "managed_landbase_by_province.csv", index=False)
    ecoprovince_out.to_csv(args.out_dir / "managed_landbase_by_ecoprovince.csv", index=False)
    summary.to_csv(args.out_dir / "managed_landbase_province_summary.csv", index=False)

    metadata = json.loads((inputs.cpcad.parent / "provenance.json").read_text()) if (inputs.cpcad.parent / "provenance.json").exists() else {}
    manifest = {
        "product": "national managed-landbase proxy",
        "status": "screening proxy; NOT a legal THLB or AAC landbase",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "resolution_m": args.resolution,
        "inputs": {
            "tenure": {
                "path": str(inputs.tenure.relative_to(REPO_ROOT)),
                "sha256": sha256(inputs.tenure),
                "bytes": inputs.tenure.stat().st_size,
                "licence": "unknown_public (GFW Canada compilation)",
            },
            "cpcad": {
                "path": str(inputs.cpcad.relative_to(REPO_ROOT)),
                "sha256": sha256(inputs.cpcad),
                "bytes": inputs.cpcad.stat().st_size,
                "licence": "Open Government Licence - Canada",
                "provenance": metadata,
            },
            "province_boundaries": {
                "path": str(inputs.province.relative_to(REPO_ROOT)),
                "sha256": sha256(inputs.province),
            },
            "ecodistricts": {
                "path": str(inputs.ecostrat.relative_to(REPO_ROOT)),
                "sha256": sha256(inputs.ecostrat),
            },
            "scanfi_area_weights": {
                "path": str(inputs.area_weights.relative_to(REPO_ROOT)),
                "sha256": sha256(inputs.area_weights),
            },
        },
        "outputs": {
            "by_province": "managed_landbase_by_province.csv",
            "by_ecoprovince": "managed_landbase_by_ecoprovince.csv",
            "province_summary": "managed_landbase_province_summary.csv",
        },
        "validation": checks,
        "caveats": [
            "Tenure polygons are a 2016 national compilation, not a legal tenure registry.",
            "Class footprints overlap; managed_proxy_ha is apportioned to the tenure union.",
            "CPCAD protected areas are excluded in full; IUCN VI areas that may permit forestry are therefore conservative.",
            "Forest-area equivalents assume forest is uniformly distributed within an ecodistrict.",
            "No operability, accessibility, economic, or AAC constraints are applied.",
            "No tenure coverage for NU or PE; values are blank, not zero.",
        ],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = build_report(summary, province_out, checks, args.resolution)
    (args.out_dir / "NATIONAL_MANAGED_LANDBASE_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps(checks, indent=2))


def build_report(summary: pd.DataFrame, province_out: pd.DataFrame, checks: dict, resolution: int) -> str:
    table = summary.copy()
    table["managed_share"] = (table["managed_share"] * 100).round(1)
    for column in ["tenure_union_footprint_ha", "tenure_union_forest_ha", "protected_excluded_ha", "managed_proxy_ha", "scanfi_forest_area_ha"]:
        table[column] = (table[column] / 1e6).round(2)
    table = table.rename(
        columns={
            "tenure_union_footprint_ha": "tenure footprint (Mha)",
            "tenure_union_forest_ha": "tenure forest (Mha)",
            "protected_excluded_ha": "protected excluded (Mha)",
            "managed_proxy_ha": "managed proxy (Mha)",
            "scanfi_forest_area_ha": "SCANFI forest (Mha)",
            "managed_share": "managed share (%)",
            "coverage": "coverage",
        }
    )
    table = table.fillna("missing")
    lines = [
        "# National Managed-Landbase Proxy",
        "",
        "**Status: screening proxy. This is not a legal timber harvesting landbase (THLB), not an AAC landbase, and is not applied to the 150 Mt supply estimate.**",
        "",
        f"Grid resolution: {resolution} m. Tenure vintage: 2016. Protected areas: CPCAD (2026-11-18 REST snapshot).",
        "",
        "## Method",
        "",
        "1. Compiled GFW Canada Forest Tenures 2016 polygons were deduplicated by exact geometry, repaired, and rasterized onto a Canada Albers grid.",
        "2. CPCAD protected and conserved areas were rasterized on the same grid and subtracted wherever they overlap tenure.",
        "3. Each cell was assigned to a province and SCANFI ecodistrict; SCANFI ecodistrict forest area was converted to a forest fraction.",
        "4. Class footprints overlap (nested area-based and volume-based tenures). `forest_area_ha` is gross class footprint forest; `managed_proxy_ha` is apportioned to the tenure union so class rows sum to the union and do not double count.",
        "5. `managed_share` uses SCANFI forest area for the province or ecoprovince as denominator.",
        "",
        "Evidence tiers: `B` = accepted national tenure compilation (2016) with CPCAD protected overlay, suitable for screening only; `missing` = no accepted tenure source and values are deliberately blank.",
        "",
        "## Provincial coverage and proxy",
        "",
        "| " + " | ".join(table.columns) + " |",
        "|" + "|".join(["---"] * len(table.columns)) + "|",
    ]
    for _, row in table.iterrows():
        lines.append("| " + " | ".join(str(value) for value in row.tolist()) + " |")
    lines += [
        "",
        "## Validation",
        "",
        f"- Tenure input features: {checks['tenure_input_features_raw']}; analysis features after exact-geometry deduplication: {checks['tenure_analysis_features']}; duplicates removed: {checks['tenure_duplicate_geometries_removed']} (mostly repeated Yukon fuel-wood polygons).",
        f"- Tenure union forest <= SCANFI forest by province: **{checks['tenure_union_forest_le_scanfi_forest']}**.",
        f"- Protected exclusion <= tenure forest: **{checks['protected_exclusion_le_tenure_forest']}**.",
        f"- Class rows sum to union proxy: **{checks['class_managed_sums_to_union']}**.",
        f"- Gross class forest / union forest ratio: {checks['class_forest_to_union_forest_ratio']:.3f} (overlap inflation before apportionment).",
        f"- National managed proxy: {checks['national_managed_proxy_ha']/1e6:.2f} Mha of {checks['national_scanfi_forest_ha']/1e6:.2f} Mha SCANFI forest.",
        f"- Missing tenure jurisdictions: {', '.join(checks['missing_jurisdictions']) or 'none'}.",
        "",
        "## Gaps and caveats",
        "",
        "- **No legal THLB**: tenure boundaries are rights areas, not operability or harvest-landbase determinations.",
        "- **No AAC**: no growth, yield, operability, accessibility, or economic constraints.",
        "- **British Columbia upper bound**: GFW BC volume-based Forest Licence and Pulpwood Agreement polygons cover almost the entire province. The BC proxy is therefore close to all unprotected forest and should be read as an upper bound on the tenured landbase, not a licence-specific footprint.",
        "- **Class overlap**: `forest_area_ha` is gross class footprint and sums to more than the tenure union in provinces with nested tenures (national ratio 1.29). Only `managed_proxy_ha` is apportioned to the union and safe to sum.",
        "- **Yukon**: the 2016 compilation contains 51 repeated fuel-wood geometries; deduplication removes the duplication, but the remaining tenure footprint remains a compilation artefact and should be treated as low confidence.",
        "- **Low tenure coverage**: NL (4.7% of SCANFI forest) and NT (1.9%) have only fragmentary 2016 tenure polygons; their proxies are lower bounds and should not be read as the managed forest area.",
        "- **Nunavut and Prince Edward Island**: no GFW 2016 tenure rows; reported as `coverage=missing` with blank values, not zero.",
        "- **Treaty/Settlement Lands**: the national forest management constraint layer was not used; Treaty/Settlement and Indigenous tenure signals are not represented.",
        "- **Protected areas**: all CPCAD polygons are excluded, including IUCN VI areas that may allow some forestry; the exclusion is conservative.",
        "- **Forest distribution**: forest-area equivalents assume forest is uniformly distributed within each ecodistrict.",
        "",
        "## Outputs",
        "",
        "- `managed_landbase_by_province.csv`: province x tenure type x tenure class rows; includes all 13 jurisdictions and explicit `coverage=missing` rows.",
        "- `managed_landbase_by_ecoprovince.csv`: same proxy aggregated to ecoprovince x province x tenure type x tenure class. It contains the 11 jurisdictions with 2016 tenure coverage; NU and PE appear only in the province file as `coverage=missing`.",
        "- `managed_landbase_province_summary.csv`: union-based provincial totals (authoritative for no-double-counting checks).",
        "- `manifest.json`: input sizes, SHA-256 hashes, licences, method settings, and validation results.",
        "",
        "## Sources and licences",
        "",
        "| source | role | size | licence |",
        "|---|---|---|---|",
        "| GFW Canada Forest Tenures 2016 (ArcGIS item `aa8797d31edd47209e8eabf1c7789e5c`) | tenure polygons, 3,680 features before deduplication | 302,119,304 bytes | unknown_public (compilation) |",
        "| CPCAD ESRI REST snapshot 2026-11-18 | protected/conserved areas, 22,438 features | 408,764,416 bytes | Open Government Licence - Canada |",
        "| SCANFI v2 national area weights | ecodistrict forest-area frame | 1,579,339 bytes (ecodistrict CSV) | Open Government Licence - Canada |",
        "| geoBoundaries CAN ADM1 simplified | province/territory polygons | 26,022,306 bytes | CC BY 4.0 |",
        "",
        "SHA-256 values and exact byte counts for every input are in `manifest.json`.",
        "",
        "## If a download is incomplete",
        "",
        "The CPCAD downloader caches every page and resumes by offset. Re-running the same command completes a partial download:",
        "",
        "```bash",
        ".venv/bin/python scripts/national/download_cpcad_rest.py --out-dir data/raw/cpcad",
        "```",
        "",
        "If the CPCAD REST service is unavailable, use the FGDB bundle from the Open Canada record, convert it to GeoPackage, and pass `--cpcad <path>` to the build script. If GFW tenure is unavailable, use the Data Basin 2012 fallback (`data/raw/tenures/databasin/`) for the nine covered provinces only; leave YT, NT, and NU as `coverage=missing` rather than fabricating coverage. The build script fails closed when a required input is absent.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python scripts/national/download_cpcad_rest.py --out-dir data/raw/cpcad",
        ".venv/bin/python scripts/national/build_managed_landbase.py --out-dir data/derived/national/managed_landbase",
        "```",
        "",
        "Raw CPCAD and tenure payloads remain in the `data` submodule and must not be committed to the parent repository.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
