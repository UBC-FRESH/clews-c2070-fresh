#!/usr/bin/env python3
"""Build the BC BCTS sale schedule, stumpage evidence, and Crown ledger sample.

Run from the repository root::

    .venv/bin/python scripts/national/build_bcts_bc.py

The script is idempotent.  Downloaded PDFs and their ``pdftotext`` extracts are
kept in the ``data`` submodule; derived CSVs and JSON land under
``data/derived/national/harvest_basis/bcts_bc``.  No bid, delivered price, or
cost is synthesized.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest import bcts_bc
from workflows.national.harvest.cost_revenue import load_ledger

RAW_DIR = REPO_ROOT / "data/raw/forest_cost_revenue/bcts_bc"
OUT_DIR = REPO_ROOT / "data/derived/national/harvest_basis/bcts_bc"

PDF_INPUTS: tuple[tuple[str, str, str], ...] = (
    (
        "interior_non_appraised_tabular_rates_2026_11.pdf",
        bcts_bc.NON_APPRAISED_PDF_URL,
        "Interior non-appraised tabular stumpage rates, effective 2026-11-01",
    ),
    (
        "interior_mps_update_2025b.pdf",
        bcts_bc.INTERIOR_MPS_URL,
        "Interior Market Pricing System update, July 1, 2025",
    ),
    (
        "coast_mps_update_2025_01.pdf",
        bcts_bc.COAST_MPS_URL,
        "Coast Market Pricing System update, January 1, 2025",
    ),
)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": "clews-c2070-fresh/0.1 (BCTS staging)"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def ensure_pdf(name: str, url: str, *, refresh: bool) -> Path:
    pdf_path = RAW_DIR / name
    if refresh or not pdf_path.exists():
        download(url, pdf_path)
    if not pdf_path.read_bytes()[:4] == b"%PDF":
        raise bcts_bc.BctsDataError(f"{pdf_path} is not a PDF; re-run with --refresh")
    return pdf_path


def ensure_text(pdf_path: Path) -> Path:
    text_path = pdf_path.with_suffix(".txt")
    if text_path.exists() and text_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return text_path
    subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), str(text_path)],
        check=True,
    )
    return text_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-download the three PDFs and re-query the ArcGIS layer",
    )
    args = parser.parse_args()

    retrieved_utc = bcts_bc.utc_now()
    query_url = f"{bcts_bc.BCTS_LAYER_URL}/query"
    source_count = bcts_bc.arcgis_count(query_url)
    features = bcts_bc.paginate_features(query_url, page_size=1000)
    sales_rows = bcts_bc.build_sales_rows(features)
    summary = bcts_bc.summarise_sales(
        sales_rows, source_count=source_count, retrieved_utc=retrieved_utc
    )

    sales_path = OUT_DIR / "bcts_sales.csv"
    bcts_bc.write_csv(sales_path, bcts_bc.SALES_FIELDS, sales_rows)
    bcts_bc.write_json(OUT_DIR / "bcts_sales_summary.json", summary)

    raw_paths: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for name, url, role in PDF_INPUTS:
        pdf_path = ensure_pdf(name, url, refresh=args.refresh)
        text_path = ensure_text(pdf_path)
        texts[pdf_path.stem] = text_path.read_text(encoding="utf-8")
        for path, kind in ((pdf_path, "pdf"), (text_path, "pdftotext_layout")):
            raw_paths.append(
                {
                    "path": str(path.relative_to(REPO_ROOT)),
                    "role": role,
                    "kind": kind,
                    "url": url,
                    "bytes": path.stat().st_size,
                    "sha256": bcts_bc.sha256_file(path),
                }
            )

    rates = bcts_bc.parse_nonappraised_text(texts["interior_non_appraised_tabular_rates_2026_11"])
    rate_rows = bcts_bc.nonappraised_rows(rates)
    rate_path = OUT_DIR / "stumpage_nonappraised_2026_11.csv"
    bcts_bc.write_csv(
        rate_path,
        ("table", "table_title", "effective_date", "forest_zone", "species", "rate_cad_per_m3"),
        rate_rows,
    )

    ledger_rows = bcts_bc.build_ledger_sample(
        rates,
        year=2026,
        effective_date="2026-11-01",
    )
    ledger_path = OUT_DIR / "ledger_sample.csv"
    bcts_bc.write_csv(ledger_path, tuple(ledger_rows[0]), ledger_rows)
    ledger = load_ledger(ledger_path)

    equations, mps_values = bcts_bc.parse_mps_updates(
        texts["interior_mps_update_2025b"],
        texts["coast_mps_update_2025_01"],
    )
    mps_payload = {
        "artifact_version": "0.1.0",
        "generated_utc": retrieved_utc,
        "sources": {
            "interior_mps_update_2025b": {
                "url": bcts_bc.INTERIOR_MPS_URL,
                "effective_date": "2025-07-01",
            },
            "coast_mps_update_2025_01": {
                "url": bcts_bc.COAST_MPS_URL,
                "effective_date": "2025-01-01",
            },
        },
        "auction_dataset_counts": {
            "interior_2010_2024": 3342,
            "coast_2006_2024": 925,
        },
        "equation_coefficients": equations,
        "representative_values": [
            {
                "document": value.document,
                "section": value.section,
                "item": value.item,
                "value_text": value.value_text,
                "value_numeric": value.value_numeric,
                "unit": value.unit,
                "notes": value.notes,
            }
            for value in mps_values
        ],
        "gaps": [
            "The MPS update papers explain the estimated-winning-bid equations; they do not publish sale-level winning bids or a reusable bid deck.",
            "Some wrapped equation labels were not captured by the line parser; captured coefficients are a subset, not a replacement for the PDF table.",
            "MPS coefficients are model inputs, not observed stumpage rates, delivered log prices, or harvest costs.",
            "The Interior and Coast papers are different vintages (2025-07-01 and 2025-01-01); do not mix them in one year series.",
        ],
    }
    bcts_bc.write_json(OUT_DIR / "mps_updates_2025.json", mps_payload)
    bcts_bc.write_csv(
        OUT_DIR / "mps_updates_2025.csv",
        ("document", "section", "item", "value_text", "value_numeric", "unit", "notes"),
        [
            {
                "document": value.document,
                "section": value.section,
                "item": value.item,
                "value_text": value.value_text,
                "value_numeric": value.value_numeric,
                "unit": value.unit,
                "notes": value.notes,
            }
            for value in mps_values
        ],
    )

    provenance = {
        "artifact_version": "0.1.0",
        "generated_utc": retrieved_utc,
        "licence": {
            "name": "Open Government Licence - British Columbia",
            "url": bcts_bc.BC_OGL_URL,
            "applies_to": [
                bcts_bc.BCTS_LAYER_URL,
                bcts_bc.NON_APPRAISED_PAGE_URL,
                bcts_bc.NON_APPRAISED_PDF_URL,
                bcts_bc.INTERIOR_MPS_URL,
                bcts_bc.COAST_MPS_URL,
            ],
        },
        "inputs": raw_paths,
        "outputs": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "rows": row_count,
                "sha256": bcts_bc.sha256_file(path),
            }
            for path, row_count in (
                (sales_path, len(sales_rows)),
                (rate_path, len(rate_rows)),
                (ledger_path, len(ledger)),
                (OUT_DIR / "mps_updates_2025.csv", len(mps_values)),
            )
        ],
        "gaps": [
            "Delivered log prices are not in any ingested source.",
            "Harvest, road, haul, and silviculture actuals are not in any ingested source.",
            "BCTS auction bid bulk data (upset, winning bid, bonus, bidder count) is absent from the ArcGIS sale-schedule layer.",
            "BCTS sales schedule has no geometry in this deliverable; only the published geographic-location text is retained.",
            "No BCTS-to-forest-zone crosswalk exists, so non-appraised zone rates are not applied to BCTS sale volumes.",
        ],
    }
    bcts_bc.write_json(OUT_DIR / "provenance.json", provenance)

    print(f"BCTS rows: {len(sales_rows)} / source count {source_count}")
    print(f"BCTS volume: {summary['volume_m3']['total']:.3f} m3")
    print(f"Non-appraised rate rows: {len(rate_rows)}")
    print(f"Ledger rows: {len(ledger)} (validated)")
    print(f"MPS coefficient rows: {len(equations)}; representative values: {len(mps_values)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
