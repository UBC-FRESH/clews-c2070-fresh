"""Download the Canadian Protected and Conserved Areas Database (CPCAD) via ESRI REST.

The Open Canada record publishes an FGDB directory and an ESRI REST endpoint.
The REST endpoint is used here because the FGDB bundle has no stable file URL.
Pages are fetched concurrently, validated for feature count, and appended to a
single GeoPackage so downstream overlays do not need to hold the full payload
in memory.

Usage:
    .venv/bin/python scripts/national/download_cpcad_rest.py \
        --out-dir data/raw/cpcad
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import requests
import shapely

SERVICE = "https://maps-cartes.ec.gc.ca/arcgis/rest/services/CWS_SCF/CPCAD/MapServer/0"
DATASET_URL = "https://open.canada.ca/data/en/dataset/6c343726-1e92-451a-876a-76e17d398a1c"
LICENCE = "Open Government Licence - Canada"
PAGE_SIZE = 1500
MAX_WORKERS = 6
FIELDS = [
    "NAME_E",
    "NAME_F",
    "JUR_ID",
    "TYPE_E",
    "IUCN_CAT",
    "STATUS",
    "ESTYEAR",
    "OWNER_E",
    "O_AREA_HA",
]


@dataclass(frozen=True)
class Page:
    offset: int
    path: Path


def request_json(url: str, params: dict, timeout: int = 240) -> dict:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_page(offset: int, page_size: int, out_dir: Path) -> list[Page]:
    suffix = "" if page_size == PAGE_SIZE else f"_n{page_size}"
    target = out_dir / f"page_{offset:06d}{suffix}.geojson"
    if target.exists() and target.stat().st_size > 0:
        return [Page(offset=offset, path=target)]

    try:
        payload = request_json(
            f"{SERVICE}/query",
            {
                "where": "1=1",
                "outFields": ",".join(FIELDS),
                "returnGeometry": "true",
                "outSR": 4326,
                "geometryPrecision": 5,
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "geojson",
            },
        )
    except requests.HTTPError:
        if page_size <= 250:
            raise
        half = page_size // 2
        return fetch_page(offset, half, out_dir) + fetch_page(offset + half, page_size - half, out_dir)

    if "features" not in payload:
        raise RuntimeError(f"CPCAD page at offset {offset} returned no features: {payload}")
    target.write_text(json.dumps(payload), encoding="utf-8")
    return [Page(offset=offset, path=target)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def page_to_frame(page: Page) -> gpd.GeoDataFrame:
    payload = json.loads(page.path.read_text(encoding="utf-8"))
    features = payload["features"]
    rows = [feature["properties"] for feature in features]
    geometries = shapely.from_geojson([json.dumps(feature["geometry"]) for feature in features])
    frame = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
    return frame.reindex(columns=FIELDS + ["geometry"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/cpcad"))
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args()

    out_dir = args.out_dir
    page_dir = out_dir / "rest_pages"
    page_dir.mkdir(parents=True, exist_ok=True)

    count_payload = request_json(
        f"{SERVICE}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    )
    feature_count = int(count_payload["count"])
    if feature_count <= 0:
        raise RuntimeError("CPCAD REST reported no features; refusing to write an empty layer")

    offsets = list(range(0, feature_count, args.page_size))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        page_groups = list(
            pool.map(lambda offset: fetch_page(offset, args.page_size, page_dir), offsets)
        )
    pages = [page for group in page_groups for page in group]

    gpkg_path = out_dir / "cpcad_rest_current.gpkg"
    if gpkg_path.exists():
        gpkg_path.unlink()
    written = 0
    for page in sorted(pages, key=lambda item: item.offset):
        frame = page_to_frame(page)
        frame.to_file(gpkg_path, layer="cpcad", driver="GPKG", mode="a" if written else "w")
        written += len(frame)

    if written != feature_count:
        raise RuntimeError(f"wrote {written} features but REST reported {feature_count}")

    metadata = {
        "dataset": "Canadian Protected and Conserved Areas Database (CPCAD)",
        "dataset_url": DATASET_URL,
        "service_url": SERVICE,
        "licence": LICENCE,
        "retrieved_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "feature_count": feature_count,
        "query_fields": FIELDS,
        "source_crs": "EPSG:4326",
        "gpkg_bytes": gpkg_path.stat().st_size,
        "gpkg_sha256": sha256(gpkg_path),
        "pages": len(pages),
    }
    (out_dir / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(page_dir)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
