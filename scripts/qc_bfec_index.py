#!/usr/bin/env python3
"""Build a provenance index of public BFEC/Woodstock model bundles.

The BFEC diffusion site exposes one HTML directory listing per region, unit,
and (for most units) ``Modeles_compresses``. This script records the latest
ZIP per unit, the unit-page notice, and the units that publish no public model.

The index contains URLs, sizes, timestamps, and notice metadata only. It never
downloads a model bundle or cartography payload.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

INDEX_URL = "https://diffusion.mffp.gouv.qc.ca/diffusion/fec/diffusion/"
MODEL_UNITS_WITHOUT_COMPRESSED_DIR = frozenset({"08462", "08562"})
NOTICE_NAMES = ("lisez_moi.txt", "lisez_moi.pdf")
CONFIDENTIAL_MARKERS = (
    "confidentiel",
    "autorisation écrite",
    "autorisation ecrite",
    "communauté autochtone",
    "communaute autochtone",
)
ROW_RE = re.compile(
    r"(?:^|<br>)\s*(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+"
    r"(<dir>|&lt;dir&gt;|\d+)\s+<A HREF=\"([^\"]+)\">([^<]+)</A>",
    re.MULTILINE,
)


def fetch_text(url: str, attempts: int = 3) -> str:
    """Fetch a directory listing as text, retrying transient failures."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "clews-bfec-index/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read().decode("latin-1")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError("failed to fetch %s: %s" % (url, last_error))


def parse_rows(html: str) -> list[dict]:
    """Return directory rows with kind, size, name, href, and parsed timestamp."""
    rows = []
    for match in ROW_RE.finditer(html):
        date_text, time_text, size_text, href, name = match.groups()
        timestamp = datetime.strptime(
            "%s %s" % (date_text, re.sub(r"\s+", " ", time_text.strip())),
            "%m/%d/%Y %I:%M %p",
        ).replace(tzinfo=timezone.utc)
        rows.append(
            {
                "name": name,
                "href": href,
                "is_dir": "dir" in size_text,
                "bytes": None if "dir" in size_text else int(size_text),
                "timestamp": timestamp,
            }
        )
    return rows


def absolute(index_url: str, href: str) -> str:
    """Resolve an index href against the diffusion root."""
    from urllib.parse import urljoin

    return urljoin(index_url, href)


def parse_notice_kind(text: str) -> str:
    """Classify a Lisez_moi payload as confidentiality or change notice."""
    lowered = text.lower()
    if any(marker in lowered for marker in CONFIDENTIAL_MARKERS):
        return "confidentiality"
    return "modification"


def fetch_notice(unit_url: str, rows: list[dict]) -> dict:
    """Record the unit-page notice without retaining its full text."""
    notice_files = sorted(
        (row for row in rows if row["name"].lower() in NOTICE_NAMES),
        key=lambda row: (not row["name"].lower().endswith(".txt"), row["name"]),
    )
    if not notice_files:
        return {"notice_present": False}
    notice_url = absolute(unit_url, notice_files[0]["href"])
    text = ""
    if notice_files[0]["name"].lower().endswith(".txt"):
        text = fetch_text(notice_url)
    return {
        "notice_present": True,
        "notice_kind": parse_notice_kind(text) if text else "unknown",
        "notice_url": notice_url,
        "notice_bytes": notice_files[0]["bytes"],
        "notice_sha256": _sha256_text(text) if text else None,
    }


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def zip_candidates(rows: list[dict]) -> list[dict]:
    """Return ZIP rows ordered newest first."""
    zips = [
        row for row in rows
        if not row["is_dir"] and row["name"].lower().endswith(".zip")
    ]
    return sorted(zips, key=lambda row: (row["timestamp"], row["name"]), reverse=True)


def build_index(index_url: str) -> dict:
    """Crawl the diffusion index and return a unit-by-unit source index."""
    regions = [row for row in parse_rows(fetch_text(index_url)) if row["is_dir"]]
    units: list[dict] = []
    skipped: list[dict] = []
    for region in regions:
        region_url = absolute(index_url, region["href"])
        for unit_row in parse_rows(fetch_text(region_url)):
            if not unit_row["is_dir"]:
                continue
            unit = unit_row["name"]
            unit_url = absolute(index_url, unit_row["href"])
            unit_rows = parse_rows(fetch_text(unit_url))
            notice = fetch_notice(unit_url, unit_rows)
            compressed = next(
                (row for row in unit_rows if row["is_dir"] and row["name"] == "Modeles_compresses"),
                None,
            )
            if compressed is None and unit not in MODEL_UNITS_WITHOUT_COMPRESSED_DIR:
                skipped.append(
                    {
                        "unit": unit,
                        "region": region["name"],
                        "unit_url": unit_url,
                        "reason": "no_public_model_directory",
                        **notice,
                    }
                )
                continue

            if compressed is not None:
                model_dir_url = absolute(index_url, compressed["href"])
                model_rows = parse_rows(fetch_text(model_dir_url))
                model_source = "Modeles_compresses"
            else:
                model_dir_url = unit_url
                model_rows = unit_rows
                model_source = "unit_root_zip"

            candidates = zip_candidates(model_rows)
            if not candidates:
                skipped.append(
                    {
                        "unit": unit,
                        "region": region["name"],
                        "unit_url": unit_url,
                        "reason": "public_model_directory_without_zip",
                        **notice,
                    }
                )
                continue
            latest = candidates[0]
            units.append(
                {
                    "unit": unit,
                    "region": region["name"],
                    "unit_url": unit_url,
                    "model_source": model_source,
                    "model_dir_url": model_dir_url,
                    "url": absolute(index_url, latest["href"]),
                    "file": latest["name"],
                    "bytes": latest["bytes"],
                    "listed_timestamp": latest["timestamp"].isoformat(),
                    "candidate_count": len(candidates),
                    "candidates": [
                        {
                            "file": candidate["name"],
                            "bytes": candidate["bytes"],
                            "listed_timestamp": candidate["timestamp"].isoformat(),
                        }
                        for candidate in candidates
                    ],
                    **notice,
                }
            )

    units.sort(key=lambda item: item["unit"])
    skipped.sort(key=lambda item: item["unit"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "index_url": index_url,
        "units": units,
        "skipped_units": skipped,
        "counts": {
            "regions": len(regions),
            "units_with_public_model": len(units),
            "units_with_compressed_dir": sum(
                1 for item in units if item["model_source"] == "Modeles_compresses"
            ),
            "units_without_public_model": len(skipped),
            "listed_bytes": sum(item["bytes"] for item in units),
        },
        "policy": {
            "model_units_without_compressed_dir": sorted(MODEL_UNITS_WITHOUT_COMPRESSED_DIR),
            "no_model_units_downloaded": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-url", default=INDEX_URL)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    index = build_index(args.index_url.rstrip("/") + "/")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(index["counts"], indent=2))
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
