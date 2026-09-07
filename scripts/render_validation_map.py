#!/usr/bin/env python3
"""Render quick human-inspection maps for model validation artifacts.

The default basemap is Natural Earth's public-domain 1:10m administrative,
coastline, and populated-place data. Downloads are cached outside the
repository under ``~/.cache/clews-c2070-basemaps`` (or ``CLEWS_BASEMAP_CACHE``).
The basemap is deliberately pale so model geometry remains visually dominant.

For more detailed regional orientation, ``--basemap osm`` uses Contextily and
OpenStreetMap tiles. OSM attribution is drawn on the figure and the tile cache
also remains outside the repository.
"""

from __future__ import annotations

import argparse
import csv
import os
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv"
DEFAULT_MANIFEST_ROOT = REPO_ROOT / "data/manifests/scanfi-diagnostic/tiny-bc-real-sampling"
DEFAULT_OUTPUT = REPO_ROOT / "tmp/validation-maps/scanfi-six-cells.png"
BASEMAP_CACHE = Path(os.environ.get("CLEWS_BASEMAP_CACHE", "~/.cache/clews-c2070-basemaps")).expanduser()
SCANFI_SOURCE_CRS = "+proj=lcc +lat_0=0 +lon_0=-95 +lat_1=49 +lat_2=77 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs"

NATURAL_EARTH = {
    "admin1": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip",
    "countries": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip",
    "coastline": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_coastline.zip",
    "lakes": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_lakes.zip",
    "places": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_populated_places.zip",
}


def cached_layer(name: str) -> gpd.GeoDataFrame:
    """Download one Natural Earth layer once, then read it from user cache."""
    target = BASEMAP_CACHE / name
    target.mkdir(parents=True, exist_ok=True)
    shp = next(target.glob("*.shp"), None)
    if shp is None:
        archive = target / f"{name}.zip"
        if not archive.exists():
            print(f"downloading {name} basemap layer to {archive}")
            urllib.request.urlretrieve(NATURAL_EARTH[name], archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        shp = next(target.glob("*.shp"), None)
    if shp is None:
        raise RuntimeError(f"Natural Earth archive did not contain a shapefile: {name}")
    return gpd.read_file(shp)


def read_windows(inventory_path: Path, manifest_root: Path) -> gpd.GeoDataFrame:
    """Read EPSG:3978 window bounds from the per-cell manifests."""
    rows: list[dict[str, object]] = []
    with inventory_path.open(newline="", encoding="utf-8") as handle:
        inventory = list(csv.DictReader(handle))
    for cell in inventory:
        cell_id = cell["inventory_cell_id"]
        manifest = manifest_root / f"{cell_id}.csv"
        if not manifest.exists():
            raise FileNotFoundError(f"Missing window manifest for {cell_id}: {manifest}")
        with manifest.open(newline="", encoding="utf-8") as handle:
            first = next(csv.DictReader(handle))
        xmin, ymin, xmax, ymax = (float(x) for x in first["window_bounds_lcc"].split(","))
        rows.append({
            "inventory_cell_id": cell_id,
            "diagnostic_region": cell["diagnostic_region"],
            "geometry": box(xmin, ymin, xmax, ymax),
        })
    return gpd.GeoDataFrame(rows, crs=SCANFI_SOURCE_CRS)


def add_natural_earth(ax, target_crs: str) -> None:
    """Draw a pale, recognizable Canada/BC reference basemap."""
    countries = cached_layer("countries").to_crs(target_crs)
    provinces = cached_layer("admin1").to_crs(target_crs)
    coastline = cached_layer("coastline").to_crs(target_crs)
    lakes = cached_layer("lakes").to_crs(target_crs)
    places = cached_layer("places").to_crs(target_crs)

    countries[countries.get("ADM0_A3") == "CAN"].plot(ax=ax, color="#f1f3f0", edgecolor="#aab2ae", linewidth=0.55, zorder=1)
    lakes.plot(ax=ax, color="#dce9ed", edgecolor="none", alpha=0.75, zorder=2)
    provinces[provinces.get("adm0_a3") == "CAN"].plot(ax=ax, facecolor="none", edgecolor="#b8bfbb", linewidth=0.35, zorder=3)
    bc = provinces[(provinces.get("adm0_a3") == "CAN") & (provinces.get("name_en") == "British Columbia")]
    bc.plot(ax=ax, facecolor="#e6ebe6", edgecolor="#68756f", linewidth=0.9, zorder=4)
    coastline.plot(ax=ax, color="#87938d", linewidth=0.35, alpha=0.8, zorder=5)

    canada_places = places[places.get("ADM0_A3") == "CAN"].copy()
    if "SCALERANK" in canada_places:
        canada_places = canada_places[canada_places["SCALERANK"] <= 5]
    canada_places.plot(ax=ax, color="#7b8580", markersize=4, alpha=0.55, zorder=6)
    for _, row in canada_places.iterrows():
        ax.annotate(str(row.get("NAME", "")), (row.geometry.x, row.geometry.y), xytext=(2, 2), textcoords="offset points", fontsize=5.5, color="#68716c", alpha=0.75, zorder=7)


def render(inventory: Path, manifest_root: Path, output: Path, basemap: str) -> None:
    windows = read_windows(inventory, manifest_root)
    windows_ll = windows.to_crs("EPSG:4326")
    # Fit a BC-wide frame with a modest margin, while preserving the provincial context.
    minx, miny, maxx, maxy = windows.total_bounds
    frame = gpd.GeoSeries([box(minx, miny, maxx, maxy)], crs=windows.crs).to_crs("EPSG:4326").total_bounds
    lon_min, lat_min, lon_max, lat_max = frame
    lon_pad = max(2.0, (lon_max - lon_min) * 0.35)
    lat_pad = max(1.5, (lat_max - lat_min) * 0.30)

    fig, ax = plt.subplots(figsize=(12, 9), constrained_layout=True)
    if basemap == "natural-earth":
        add_natural_earth(ax, "EPSG:4326")
    elif basemap == "osm":
        import contextily as ctx
        # Contextily needs a projected axis; use Web Mercator for tiles.
        ax.set_xlim(lon_min - lon_pad, lon_max + lon_pad)
        ax.set_ylim(lat_min - lat_pad, lat_max + lat_pad)
        ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik, alpha=0.28, attribution="© OpenStreetMap contributors", crs="EPSG:4326")
    else:
        raise ValueError(f"Unknown basemap: {basemap}")

    colors = ["#d73027", "#4575b4", "#1a9850", "#984ea3", "#ff7f00", "#a65628"]
    windows_ll.plot(ax=ax, facecolor="none", edgecolor=colors[: len(windows_ll)], linewidth=2.2, zorder=10)
    for i, (_, row) in enumerate(windows_ll.iterrows(), start=1):
        point = row.geometry.centroid
        ax.annotate(f"{i}. {row['diagnostic_region']}", (point.x, point.y), xytext=(5, 5), textcoords="offset points", fontsize=8, weight="bold", color=colors[i - 1], bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "none", "pad": 1.5}, zorder=11)

    ax.set_xlim(lon_min - lon_pad, lon_max + lon_pad)
    ax.set_ylim(lat_min - lat_pad, lat_max + lat_pad)
    ax.set_xlabel("Longitude (degrees)")
    ax.set_ylabel("Latitude (degrees)")
    ax.set_title("SCANFI diagnostic sampling windows — British Columbia context", fontsize=15, weight="bold")
    ax.text(0.01, 0.01, "Windows: SCANFI source Lambert CRS, 30 m source grid; basemap is orientation only", transform=ax.transAxes, fontsize=8, color="#59635e")
    if basemap == "natural-earth":
        ax.text(0.99, 0.01, "Basemap: Natural Earth 1:10m (public domain)", transform=ax.transAxes, ha="right", fontsize=8, color="#59635e")
    ax.grid(color="#9ba59f", linewidth=0.3, alpha=0.35)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, facecolor="white")
    print(f"wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--manifest-root", type=Path, default=DEFAULT_MANIFEST_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--basemap", choices=("natural-earth", "osm"), default="natural-earth")
    args = parser.parse_args()
    render(args.inventory, args.manifest_root, args.output, args.basemap)


if __name__ == "__main__":
    main()
