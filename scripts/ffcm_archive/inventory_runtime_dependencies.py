#!/usr/bin/env python3
"""Inventory FCM Java runtime jars without copying runtime payloads."""

from __future__ import annotations

import argparse
import csv
import zipfile
from pathlib import Path


def classify_jar(path: Path) -> tuple[str, str]:
    name = path.name.lower()
    if name.startswith("fcm-"):
        return "application-runtime", "FCM/JaamSim runtime jar; inspect as implementation evidence only."
    if "cplex" in name:
        return "proprietary-solver", "IBM CPLEX Java API; license boundary, do not port as a FRESH dependency."
    if "lpsolve" in name:
        return "legacy-solver", "lp_solve Java binding; solver evidence, not preferred FRESH dependency."
    if "postgresql" in name:
        return "database-driver", "PostgreSQL JDBC driver; evidence of relational database integration."
    if "mongo" in name:
        return "database-driver", "MongoDB Java driver; evidence of document database integration."
    if "worldwind" in name:
        return "gis-visualization", "NASA WorldWind dependency; visualization/GIS runtime evidence."
    if "jogl" in name or "gluegen" in name:
        return "native-graphics", "OpenGL/native graphics dependency used by visualization stack."
    if name.startswith("commons-") or "json-simple" in name:
        return "utility-library", "General Java utility dependency."
    return "runtime-library", "Unclassified Java runtime dependency."


def manifest_fields(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                text = zf.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
            except KeyError:
                return fields
    except zipfile.BadZipFile:
        return fields
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def zip_entry_count(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            return str(len(zf.infolist()))
    except zipfile.BadZipFile:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path, default=Path("tmp/FCM/FCM_Releases"))
    parser.add_argument("--out", type=Path, default=Path("metadata/ffcm-archive/runtime-dependencies.csv"))
    args = parser.parse_args()

    jars = sorted(args.runtime_dir.glob("*.jar")) + sorted((args.runtime_dir / "FCM-1908_lib").glob("*.jar"))
    rows: list[dict[str, str]] = []
    for jar in jars:
        category, note = classify_jar(jar)
        manifest = manifest_fields(jar)
        rows.append(
            {
                "relative_path": str(jar),
                "bytes": str(jar.stat().st_size),
                "zip_entry_count": zip_entry_count(jar),
                "category": category,
                "implementation_note": note,
                "manifest_title": manifest.get("Implementation-Title", ""),
                "manifest_version": manifest.get("Implementation-Version", ""),
                "main_class": manifest.get("Main-Class", ""),
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "relative_path",
                "bytes",
                "zip_entry_count",
                "category",
                "implementation_note",
                "manifest_title",
                "manifest_version",
                "main_class",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
