#!/usr/bin/env python3
"""Extract observed Quebec BMMB delivered-to-destination sale results.

The BMMB archive export is a public legacy ``.xls`` workbook with one row per
auctioned sector.  This extractor publishes the delivered subset separately
from standing and ``Récolté`` sales, at the aggregation level the source
actually supports.

The delivered lot price is a total sale value.  The source does not publish a
species- or grade-specific price for these lots, so the sale table carries one
aggregate CAD/m3 rate and the species file carries unpriced volume lines only.
No price is allocated across species or grades.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import xlrd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "data/derived/national/harvest_basis/bmmb_delivered"
DEFAULT_EXPORT = REPO_ROOT / "tmp/bmmb/export_archive.raw"
DEFAULT_DETAILS = REPO_ROOT / "tmp/bmmb/details"
DEFAULT_DETAIL_META = REPO_ROOT / "tmp/bmmb/detail_meta.tsv"
DEFAULT_PDFS = REPO_ROOT / "tmp/bmmb"

SHEET_NAME = "Sheet0"

DELIVERED = "Livré à destination"
RECOLTE = "Récolté"
STANDING_MEASURED = "Sur pied selon mesurage"
STANDING_INVENTORY = "Sur pied selon inventaire"

SALE_TYPE_FAMILIES = {
    DELIVERED: "delivered_to_destination",
    RECOLTE: "harvested_recolte_unspecified",
    STANDING_MEASURED: "standing_measured",
    STANDING_INVENTORY: "standing_inventory",
}

VALUE_SEMANTICS = {
    "delivered_to_destination": "delivered_sale_total_cad",
    "harvested_recolte_unspecified": "recolte_sale_total_cad_basis_unspecified",
    "standing_measured": "standing_sale_total_cad",
    "standing_inventory": "standing_sale_total_cad",
}

RATE_UNIT_BASIS = {
    "delivered_to_destination": "CAD_per_m3_delivered_destination",
    "harvested_recolte_unspecified": "CAD_per_m3_unspecified",
    "standing_measured": "CAD_per_m3_standing",
    "standing_inventory": "CAD_per_m3_standing",
}

SOURCE_URL = "https://bmmb.gouv.qc.ca/Auction/Auctions/Archive"
EXPORT_URL = "https://bmmb.gouv.qc.ca/Auction/Auctions/Export?IsArchive=True"
EXPORT_FINAL_URL = (
    "https://encheres.forets.gouv.qc.ca/Auction/Auctions/Export?IsArchive=True"
)
EXPORT_HTTP_STATUS = 200
EXPORT_CONTENT_TYPE = "application/vnd.ms-excel"
EXPORT_CONTENT_DISPOSITION = "attachment; filename=VentesArchivees.xls"
EXPORT_BYTES = 9_795_584
EXPORT_SHA256 = "48c11fff0cb0f556be4d81cb454ff1468a51df4493b3a2c13ef3a58b72b2b3ce"
EXPORT_RETRIEVED_UTC = "2026-11-21T20:29:05Z"

ARCHIVE_CONTENT_TYPE = "text/html; charset=utf-8"
ARCHIVE_BYTES = 52_875
ARCHIVE_SHA256 = "75ab4d0d49c1266b8b6f185a4f78360d26e7bbd93727b6029cc3abdbf2914413"
ARCHIVE_RETRIEVED_UTC = "2026-11-21T20:29:23Z"

SALE_COLUMNS = (
    "sale_id",
    "sale_year",
    "auction_end_date",
    "auction_end_year",
    "year_mismatch",
    "region_bmmb",
    "ua",
    "sector_name",
    "latitude",
    "longitude",
    "tenure",
    "auction_type",
    "sale_type",
    "sale_type_family",
    "status",
    "awarded",
    "volume_m3",
    "volume_optional_m3",
    "total_cad",
    "price_cad_m3",
    "price_cad_m3_lower_bound",
    "price_unit_basis",
    "partial_award",
    "currency_basis",
    "observed",
    "observation_type",
    "price_source",
    "price_source_url",
    "estimated_total_cad",
    "estimated_cad_m3",
    "product_scope",
    "species_scope",
    "quality",
    "aggregation_level",
    "species_volume_line_count",
    "detail_url",
    "export_row_number",
    "notes",
)

SPECIES_COLUMNS = (
    "sale_id",
    "sale_year",
    "species",
    "product",
    "requirement",
    "volume_m3",
    "quality",
    "price_cad_m3",
    "total_cad",
    "observed_volume",
    "observed_price",
    "aggregation_level",
    "source",
)

SUMMARY_COLUMNS = (
    "sale_type",
    "sale_type_family",
    "sale_year",
    "sale_count",
    "awarded_count",
    "unsold_count",
    "cancelled_count",
    "other_status_count",
    "observed_price_count",
    "partial_award_count",
    "volume_m3",
    "volume_optional_m3",
    "volume_awarded_m3",
    "volume_awarded_priced_m3",
    "rate_computable_volume_m3",
    "reported_sale_value_cad",
    "rate_computable_value_cad",
    "partial_award_value_cad",
    "rate_cad_m3",
    "value_semantics",
    "rate_unit_basis",
    "is_delivered",
    "notes",
)

SPECIES_COLUMN_PATTERN = re.compile(
    r"^(?P<species>.+?) - Volume de "
    r"(?P<product>bois d'oeuvre|trituration) "
    r"(?P<requirement>obligatoire|optionnel)$"
)
SALE_ID_PATTERN = re.compile(r"^([A-Z]{2})(\d{4})([A-Z0-9]+)$")
RESULT_PRICE_PATTERN = re.compile(
    r"La vente a été adjugée à .*? pour ([\d\s\u00a0,]+?)\s*\$"
)
PDF_PRICE_PATTERN = re.compile(r"Prix de vente\s*:\s*([\d\s\u00a0,]+?)\s*\$")

PDF_RESULTS = {
    "CS201206004": {
        "url": (
            "https://bmmb.gouv.qc.ca/Auction/File/GetHashable/mBd9BQq1g9MoQnSHIOsUAQ"
        ),
        "filename": "result_CS201206004.pdf",
        "sha256": ("0f5c16ca3c79fef06acc5b31a7411a43fb4b50322d96ee6440b0c2b36f800101"),
        "bytes": 173_296,
        "http_status": 200,
        "content_type": "application/pdf",
        "retrieved_utc": "2026-11-21T20:30:23Z",
    },
    "CS201207001": {
        "url": (
            "https://bmmb.gouv.qc.ca/Auction/File/GetHashable/oexkf1webqUU3HnRc1DggA"
        ),
        "filename": "result_CS201207001.pdf",
        "sha256": ("2038759b3262627704e1ca3f957706b9ff21bd9933f8d6cc4a86e716b4d096b1"),
        "bytes": 175_176,
        "http_status": 200,
        "content_type": "application/pdf",
        "retrieved_utc": "2026-11-21T20:30:28Z",
    },
}

REQUIRED_EXPORT_COLUMNS = (
    "Territoire",
    "Numéro d'appel d'offres",
    "Nom du secteur",
    "Etat",
    "Prix de vente",
    "Statut des opérations",
    "Type de vente",
    "Type d'enchère",
    "Provenance",
    "UA",
    "Superficie",
    "Prix estimé",
    "Prix de départ",
    "Montant de la garantie",
    "Volume obligatoire total m³",
    "Volume optionnel total m³",
    "Date de fin enchère",
    "Latitude",
    "Longitude",
)


@dataclass(frozen=True, slots=True)
class Sale:
    """One sale row parsed from the BMMB archive export."""

    sale_id: str
    sale_year: int
    auction_end_date: date
    region_bmmb: str
    ua: str
    sector_name: str
    latitude: float | None
    longitude: float | None
    tenure: str
    auction_type: str
    sale_type: str
    sale_type_family: str
    status: str
    awarded: bool
    volume_m3: float
    volume_optional_m3: float
    export_total_cad: float | None
    total_cad: float | None
    price_source: str
    price_source_url: str
    partial_award: bool
    estimated_total_cad: float | None
    starting_price_cad: float | None
    deposit_cad: float | None
    detail_url: str
    export_row_number: int
    species_lines: tuple[dict[str, Any], ...] = ()

    @property
    def price_cad_m3(self) -> float | None:
        """Rate only when the offered and awarded volumes are the same."""
        if self.partial_award:
            return None
        if self.total_cad is None or self.volume_m3 <= 0:
            return None
        return self.total_cad / self.volume_m3

    @property
    def price_lower_bound_cad_m3(self) -> float | None:
        """Diagnostic lower bound; offered volume can exceed awarded volume."""
        if not self.partial_award:
            return None
        if self.total_cad is None or self.volume_m3 <= 0:
            return None
        return self.total_cad / self.volume_m3

    @property
    def estimated_cad_m3(self) -> float | None:
        if self.estimated_total_cad is None or self.volume_m3 <= 0:
            return None
        return self.estimated_total_cad / self.volume_m3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_number(value: Any, field: str) -> float | None:
    """Parse one French-formatted numeric source cell."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{field}: boolean is not a number")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        cleaned = text(value).replace("\u00a0", "").replace(" ", "")
        cleaned = cleaned.replace("$", "").replace(",", ".")
        if cleaned in {"", "-", "NA", "N/A"}:
            return None
        try:
            number = float(cleaned)
        except ValueError as exc:
            raise ValueError(f"{field}: cannot parse {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field}: non-finite value {value!r}")
    return number


def parse_non_negative(value: Any, field: str) -> float | None:
    number = parse_number(value, field)
    if number is None:
        return None
    if number < 0:
        raise ValueError(f"{field}: negative value {number!r}")
    return number


def parse_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = text(value)
    if len(raw) < 10:
        raise ValueError(f"{field}: cannot parse date {value!r}")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ValueError(f"{field}: cannot parse date {value!r}") from exc


def parse_sale_year(sale_id: str) -> int:
    match = SALE_ID_PATTERN.match(sale_id)
    if match is None:
        raise ValueError(f"sale id {sale_id!r} does not encode a year")
    year = int(match.group(2))
    if not 2000 <= year <= 2100:
        raise ValueError(f"sale id {sale_id!r} has implausible year {year}")
    return year


def read_export(path: Path) -> list[dict[str, Any]]:
    workbook = xlrd.open_workbook(str(path), on_demand=True)
    if SHEET_NAME not in workbook.sheet_names():
        raise ValueError(
            f"{path}: expected sheet {SHEET_NAME!r}; found {workbook.sheet_names()!r}"
        )
    sheet = workbook.sheet_by_name(SHEET_NAME)
    headers = [text(sheet.cell_value(0, column)) for column in range(sheet.ncols)]
    if len(set(headers)) != len(headers):
        raise ValueError(f"{path}: duplicate column names in export")
    missing = [name for name in REQUIRED_EXPORT_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing!r}")

    rows: list[dict[str, Any]] = []
    for row_index in range(1, sheet.nrows):
        row = {
            header: sheet.cell_value(row_index, column)
            for column, header in enumerate(headers)
        }
        end_date = row["Date de fin enchère"]
        if isinstance(end_date, float):
            row["Date de fin enchère"] = xlrd.xldate_as_datetime(
                end_date, workbook.datemode
            )
        row["__row_number__"] = row_index + 1
        rows.append(row)
    return rows


def parse_species_lines(
    row: dict[str, Any], sale_id: str, sale_year: int
) -> tuple[dict[str, Any], ...]:
    lines: list[dict[str, Any]] = []
    mandatory_volume = 0.0
    optional_volume = 0.0
    for header, value in row.items():
        if not isinstance(header, str):
            continue
        match = SPECIES_COLUMN_PATTERN.match(header)
        if match is None:
            continue
        volume = parse_non_negative(value, f"{sale_id}/{header}")
        if volume is None or volume == 0:
            continue
        product = "sawlog" if match.group("product") == "bois d'oeuvre" else "pulpwood"
        requirement = (
            "mandatory" if match.group("requirement") == "obligatoire" else "optional"
        )
        if requirement == "mandatory":
            mandatory_volume += volume
        else:
            optional_volume += volume
        lines.append(
            {
                "sale_id": sale_id,
                "sale_year": sale_year,
                "species": match.group("species").strip(),
                "product": product,
                "requirement": requirement,
                "volume_m3": volume,
            }
        )
    expected_mandatory = parse_non_negative(
        row["Volume obligatoire total m³"], f"{sale_id}/volume"
    )
    expected_optional = parse_non_negative(
        row["Volume optionnel total m³"], f"{sale_id}/optional volume"
    )
    if expected_mandatory is None:
        raise ValueError(f"{sale_id}: missing mandatory volume")
    if abs(mandatory_volume - expected_mandatory) > 1e-6:
        raise ValueError(
            f"{sale_id}: species mandatory volume {mandatory_volume} "
            f"!= total {expected_mandatory}"
        )
    if abs(optional_volume - (expected_optional or 0.0)) > 1e-6:
        raise ValueError(
            f"{sale_id}: species optional volume {optional_volume} "
            f"!= total {expected_optional}"
        )
    return tuple(lines)


def parse_sale(
    row: dict[str, Any],
    detail_url: str,
    pdf_prices: dict[str, float],
    pdf_urls: dict[str, str],
) -> Sale:
    sale_id = text(row["Numéro d'appel d'offres"])
    if not sale_id:
        raise ValueError(f"row {row['__row_number__']}: empty sale id")
    sale_type = text(row["Type de vente"])
    family = SALE_TYPE_FAMILIES.get(sale_type)
    if family is None:
        raise ValueError(
            f"{sale_id}: unexpected sale type {sale_type!r}; "
            "extend SALE_TYPE_FAMILIES deliberately"
        )
    status = text(row["Etat"])
    awarded = status.startswith("Adjugé")
    sale_year = parse_sale_year(sale_id)
    end_date = parse_date(row["Date de fin enchère"], f"{sale_id}/end date")
    volume = parse_non_negative(row["Volume obligatoire total m³"], f"{sale_id}/volume")
    if volume is None:
        raise ValueError(f"{sale_id}: missing volume")
    if family == "delivered_to_destination" and volume <= 0:
        raise ValueError(f"{sale_id}: delivered sale volume must be positive")
    optional_volume = parse_non_negative(
        row["Volume optionnel total m³"], f"{sale_id}/optional volume"
    )
    export_total = parse_non_negative(row["Prix de vente"], f"{sale_id}/sale price")
    if (
        family == "delivered_to_destination"
        and not awarded
        and export_total is not None
    ):
        raise ValueError(
            f"{sale_id}: non-awarded delivered sale has a price {export_total}"
        )

    total_cad = export_total
    price_source = "archive_export:Prix_de_vente" if export_total is not None else ""
    price_source_url = EXPORT_URL if export_total is not None else ""
    if total_cad is None and awarded and sale_id in pdf_prices:
        total_cad = pdf_prices[sale_id]
        price_source = "result_pdf:Prix_de_vente"
        price_source_url = pdf_urls[sale_id]
    if total_cad is not None and total_cad < 0:
        raise ValueError(f"{sale_id}: negative sale price {total_cad}")

    estimated_total = parse_non_negative(
        row["Prix estimé"], f"{sale_id}/estimated price"
    )
    starting_price = parse_non_negative(
        row["Prix de départ"], f"{sale_id}/starting price"
    )
    deposit = parse_non_negative(row["Montant de la garantie"], f"{sale_id}/deposit")
    species_lines = parse_species_lines(row, sale_id, sale_year)

    return Sale(
        sale_id=sale_id,
        sale_year=sale_year,
        auction_end_date=end_date,
        region_bmmb=text(row["Territoire"]),
        ua=text(row["UA"]),
        sector_name=text(row["Nom du secteur"]),
        latitude=parse_number(row["Latitude"], f"{sale_id}/latitude"),
        longitude=parse_number(row["Longitude"], f"{sale_id}/longitude"),
        tenure=text(row["Provenance"]),
        auction_type=text(row["Type d'enchère"]),
        sale_type=sale_type,
        sale_type_family=family,
        status=status,
        awarded=awarded,
        volume_m3=volume,
        volume_optional_m3=optional_volume or 0.0,
        export_total_cad=export_total,
        total_cad=total_cad,
        price_source=price_source,
        price_source_url=price_source_url,
        partial_award=total_cad is not None and sale_id in pdf_prices,
        estimated_total_cad=estimated_total,
        starting_price_cad=starting_price,
        deposit_cad=deposit,
        detail_url=detail_url,
        export_row_number=int(row["__row_number__"]),
        species_lines=species_lines,
    )


def load_detail_meta(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    meta: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            sale_id, url, status, content_type = line.rstrip("\n").split("\t", 3)
            meta[sale_id.strip()] = {
                "url": url.strip(),
                "http_status": status.strip(),
                "content_type": content_type.strip(),
            }
    return meta


def parse_detail_price(html_path: Path) -> float | None:
    raw = html_path.read_text(encoding="utf-8")
    without_scripts = re.sub(r"<script.*?</script>", " ", raw, flags=re.DOTALL)
    without_styles = re.sub(r"<style.*?</style>", " ", without_scripts, flags=re.DOTALL)
    plain = re.sub(r"<[^>]+>", " ", without_styles)
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain)
    match = RESULT_PRICE_PATTERN.search(plain)
    if match is None:
        return None
    return parse_number(match.group(1), "detail result price")


def parse_pdf_price(pdf_path: Path) -> float:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = PDF_PRICE_PATTERN.search(result.stdout)
    if match is None:
        raise ValueError(f"{pdf_path}: no 'Prix de vente' line")
    price = parse_number(match.group(1), f"{pdf_path}/price")
    if price is None:
        raise ValueError(f"{pdf_path}: empty 'Prix de vente' line")
    return price


def load_pdf_prices(pdf_dir: Path) -> tuple[dict[str, float], dict[str, str]]:
    prices: dict[str, float] = {}
    urls: dict[str, str] = {}
    for sale_id, meta in PDF_RESULTS.items():
        path = pdf_dir / meta["filename"]
        if not path.exists():
            continue
        actual_hash = sha256_file(path)
        if actual_hash != meta["sha256"]:
            raise ValueError(
                f"{path}: sha256 {actual_hash} != expected {meta['sha256']}"
            )
        prices[sale_id] = parse_pdf_price(path)
        urls[sale_id] = meta["url"]
    return prices, urls


def validate_detail_prices(
    sales: list[Sale], details_dir: Path
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for sale in sales:
        if sale.total_cad is None or sale.price_source.startswith("result_pdf"):
            continue
        path = details_dir / f"{sale.sale_id}.html"
        if not path.exists():
            continue
        detail_price = parse_detail_price(path)
        if detail_price is None:
            raise ValueError(
                f"{sale.sale_id}: export has a price but detail page has no result"
            )
        if abs(detail_price - sale.total_cad) > 0.005:
            raise ValueError(
                f"{sale.sale_id}: detail price {detail_price} "
                f"!= export price {sale.total_cad}"
            )
        checks.append(
            {
                "sale_id": sale.sale_id,
                "export_total_cad": sale.total_cad,
                "detail_total_cad": detail_price,
                "status": "pass",
            }
        )
    return checks


def format_number(value: float | None, places: int = 3) -> str:
    if value is None:
        return ""
    rendered = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return rendered or "0"


def product_scope(sale: Sale) -> str:
    products = {line["product"] for line in sale.species_lines}
    if products == {"sawlog", "pulpwood"}:
        return "sawlog_and_pulpwood"
    if products == {"sawlog"}:
        return "sawlog"
    if products == {"pulpwood"}:
        return "pulpwood"
    return "unspecified"


def sale_notes(sale: Sale) -> str:
    notes = [
        "currency_basis=CAD_nominal",
        "sale_type_implies_delivery=1",
        "service_bundle_not_published=1",
        "destination_not_reported=1",
        "quality_not_reported=1",
        "volume_denominator=mandatory_offered_volume",
        "not_stumpage=1",
        "not_mill_gate_confirmed=1",
    ]
    if sale.total_cad is not None:
        notes.append("price_is_total_sale_value=1")
        notes.append("delivery_service_included_by_sale_type=1")
    else:
        notes.append("price_is_total_sale_value=0")
        notes.append("offer_only_not_observed=1")
    if sale.partial_award:
        notes.append("partial_award_noted=1")
        notes.append("rate_not_computable_partial_award=1")
        notes.append("rate_lower_bound_uses_total_offered_volume=1")
    if sale.tenure != "Forêt publique":
        notes.append(f"tenure={sale.tenure}")
    if sale.volume_optional_m3:
        notes.append("optional_volume_excluded_from_rate=1")
    return "; ".join(notes)


def sale_row(sale: Sale) -> dict[str, Any]:
    observed = sale.total_cad is not None
    if observed:
        observation_type = "observed_sale_price"
    elif sale.estimated_total_cad is not None:
        observation_type = "offer_estimate_only"
    else:
        observation_type = "not_awarded_no_price"
    return {
        "sale_id": sale.sale_id,
        "sale_year": sale.sale_year,
        "auction_end_date": sale.auction_end_date.isoformat(),
        "auction_end_year": sale.auction_end_date.year,
        "year_mismatch": int(sale.sale_year != sale.auction_end_date.year),
        "region_bmmb": sale.region_bmmb,
        "ua": sale.ua,
        "sector_name": sale.sector_name,
        "latitude": format_number(sale.latitude, 6),
        "longitude": format_number(sale.longitude, 6),
        "tenure": sale.tenure,
        "auction_type": sale.auction_type,
        "sale_type": sale.sale_type,
        "sale_type_family": sale.sale_type_family,
        "status": sale.status,
        "awarded": int(sale.awarded),
        "volume_m3": format_number(sale.volume_m3, 3),
        "volume_optional_m3": format_number(sale.volume_optional_m3, 3),
        "total_cad": format_number(sale.total_cad, 2),
        "price_cad_m3": format_number(sale.price_cad_m3, 4),
        "price_cad_m3_lower_bound": format_number(sale.price_lower_bound_cad_m3, 4),
        "price_unit_basis": (
            "not_computable_partial_award_volume"
            if sale.partial_award
            else "CAD_per_m3_delivered_destination"
            if observed
            else ""
        ),
        "partial_award": int(sale.partial_award),
        "currency_basis": "CAD_nominal",
        "observed": int(observed),
        "observation_type": observation_type,
        "price_source": sale.price_source,
        "price_source_url": sale.price_source_url,
        "estimated_total_cad": format_number(sale.estimated_total_cad, 2),
        "estimated_cad_m3": format_number(sale.estimated_cad_m3, 4),
        "product_scope": product_scope(sale),
        "species_scope": "ALL_SPECIES_MIXED",
        "quality": "NA",
        "aggregation_level": "sale_total",
        "species_volume_line_count": len(sale.species_lines),
        "detail_url": sale.detail_url,
        "export_row_number": sale.export_row_number,
        "notes": sale_notes(sale),
    }


def species_rows(sales: list[Sale]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sale in sales:
        for line in sale.species_lines:
            rows.append(
                {
                    "sale_id": sale.sale_id,
                    "sale_year": sale.sale_year,
                    "species": line["species"],
                    "product": line["product"],
                    "requirement": line["requirement"],
                    "volume_m3": format_number(line["volume_m3"], 3),
                    "quality": "NA",
                    "price_cad_m3": "",
                    "total_cad": "",
                    "observed_volume": 1,
                    "observed_price": 0,
                    "aggregation_level": "sale_species_product_volume",
                    "source": "archive_export:species_volume_columns",
                }
            )
    return rows


def summarise_sales(sales: list[Sale]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[Sale]] = defaultdict(list)
    for sale in sales:
        groups[(sale.sale_type, sale.sale_year)].append(sale)

    summary: list[dict[str, Any]] = []
    for (sale_type, sale_year), group in sorted(
        groups.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        family = SALE_TYPE_FAMILIES[sale_type]
        awarded = [sale for sale in group if sale.awarded]
        priced = [sale for sale in awarded if sale.total_cad is not None]
        rate_priced = [sale for sale in priced if not sale.partial_award]
        partial_priced = [sale for sale in priced if sale.partial_award]
        value = sum(sale.total_cad for sale in priced if sale.total_cad is not None)
        rate_value = sum(
            sale.total_cad for sale in rate_priced if sale.total_cad is not None
        )
        partial_value = sum(
            sale.total_cad for sale in partial_priced if sale.total_cad is not None
        )
        priced_volume = sum(sale.volume_m3 for sale in priced)
        rate_volume = sum(sale.volume_m3 for sale in rate_priced)
        rate = rate_value / rate_volume if rate_volume > 0 else None
        summary.append(
            {
                "sale_type": sale_type,
                "sale_type_family": family,
                "sale_year": sale_year,
                "sale_count": len(group),
                "awarded_count": len(awarded),
                "unsold_count": sum(1 for sale in group if sale.status == "Non-adjugé"),
                "cancelled_count": sum(
                    1 for sale in group if "annulé" in sale.status.lower()
                ),
                "other_status_count": sum(
                    1
                    for sale in group
                    if not sale.awarded
                    and sale.status != "Non-adjugé"
                    and "annulé" not in sale.status.lower()
                ),
                "observed_price_count": len(priced),
                "partial_award_count": len(partial_priced),
                "volume_m3": format_number(sum(sale.volume_m3 for sale in group), 3),
                "volume_optional_m3": format_number(
                    sum(sale.volume_optional_m3 for sale in group), 3
                ),
                "volume_awarded_m3": format_number(
                    sum(sale.volume_m3 for sale in awarded), 3
                ),
                "volume_awarded_priced_m3": format_number(priced_volume, 3),
                "rate_computable_volume_m3": format_number(rate_volume, 3),
                "reported_sale_value_cad": format_number(value, 2),
                "rate_computable_value_cad": format_number(rate_value, 2),
                "partial_award_value_cad": format_number(partial_value, 2),
                "rate_cad_m3": format_number(rate, 4),
                "value_semantics": VALUE_SEMANTICS[family],
                "rate_unit_basis": RATE_UNIT_BASIS[family],
                "is_delivered": int(family == "delivered_to_destination"),
                "notes": (
                    "sale_price_is_not_delivered=1"
                    if family != "delivered_to_destination"
                    else "sale_price_is_delivered_total=1; "
                    "price_not_allocated_by_species=1; "
                    "partial_award_value_excluded_from_rate=1"
                ),
            }
        )
    return summary


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def validate(
    sales: list[Sale],
    summary: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    detail_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(source_rows) != 3_796:
        raise ValueError(f"source row count changed: {len(source_rows)} != 3796")
    if len(sales) != 18:
        raise ValueError(f"delivered sale count changed: {len(sales)} != 18")
    if len({sale.sale_id for sale in sales}) != len(sales):
        raise ValueError("duplicate delivered sale ids")
    source_ids = [text(row["Numéro d'appel d'offres"]) for row in source_rows]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("duplicate sale ids in export")

    for sale in sales:
        if sale.sale_type != DELIVERED:
            raise ValueError(f"{sale.sale_id}: not a delivered sale")
        if sale.sale_type_family != "delivered_to_destination":
            raise ValueError(f"{sale.sale_id}: wrong sale type family")
        if sale.volume_m3 <= 0 or not math.isfinite(sale.volume_m3):
            raise ValueError(f"{sale.sale_id}: invalid volume")
        if sale.total_cad is not None and sale.total_cad < 0:
            raise ValueError(f"{sale.sale_id}: negative total")
        if sale.price_cad_m3 is not None and sale.price_cad_m3 < 0:
            raise ValueError(f"{sale.sale_id}: negative unit price")
        if (
            sale.price_lower_bound_cad_m3 is not None
            and sale.price_lower_bound_cad_m3 < 0
        ):
            raise ValueError(f"{sale.sale_id}: negative lower-bound price")
        if sale.partial_award and sale.price_cad_m3 is not None:
            raise ValueError(f"{sale.sale_id}: partial award has a full rate")
        if (
            not sale.partial_award
            and sale.total_cad is not None
            and sale.price_cad_m3 is None
        ):
            raise ValueError(f"{sale.sale_id}: missing computable rate")
        if sale.awarded and sale.total_cad is None:
            raise ValueError(f"{sale.sale_id}: awarded delivered sale has no price")
        if not sale.awarded and sale.total_cad is not None:
            raise ValueError(f"{sale.sale_id}: unsold sale has a price")
        if sale.sale_year != sale.auction_end_date.year:
            raise ValueError(f"{sale.sale_id}: sale year and end year differ")
        species_volume = sum(line["volume_m3"] for line in sale.species_lines)
        if abs(species_volume - sale.volume_m3) > 1e-6:
            raise ValueError(
                f"{sale.sale_id}: species volume {species_volume} "
                f"!= sale volume {sale.volume_m3}"
            )

    delivered_summary = [row for row in summary if row["sale_type"] == DELIVERED]
    if sum(row["sale_count"] for row in delivered_summary) != len(sales):
        raise ValueError("delivered summary count does not reconcile")
    if sum(int(row["observed_price_count"]) for row in delivered_summary) != sum(
        int(sale.total_cad is not None) for sale in sales
    ):
        raise ValueError("delivered observed price count does not reconcile")
    if sum(row["sale_count"] for row in summary) != len(source_rows):
        raise ValueError("sale type summary does not cover the export")
    source_volume = sum(
        parse_non_negative(row["Volume obligatoire total m³"], "volume") or 0.0
        for row in source_rows
    )
    if abs(sum(float(row["volume_m3"]) for row in summary) - source_volume) > 0.01:
        raise ValueError("sale type summary volume does not reconcile")

    return {
        "source_rows": len(source_rows),
        "delivered_sales": len(sales),
        "delivered_awarded": sum(1 for sale in sales if sale.awarded),
        "delivered_observed_prices": sum(
            1 for sale in sales if sale.total_cad is not None
        ),
        "delivered_volume_m3": round(sum(sale.volume_m3 for sale in sales), 3),
        "delivered_awarded_volume_m3": round(
            sum(sale.volume_m3 for sale in sales if sale.awarded), 3
        ),
        "delivered_observed_value_cad": round(
            sum(sale.total_cad for sale in sales if sale.total_cad is not None),
            2,
        ),
        "delivered_partial_award_sales": sum(1 for sale in sales if sale.partial_award),
        "delivered_partial_award_value_cad": round(
            sum(
                sale.total_cad
                for sale in sales
                if sale.partial_award and sale.total_cad is not None
            ),
            2,
        ),
        "delivered_rate_computable_volume_m3": round(
            sum(
                sale.volume_m3
                for sale in sales
                if sale.total_cad is not None and not sale.partial_award
            ),
            3,
        ),
        "delivered_rate_computable_value_cad": round(
            sum(
                sale.total_cad
                for sale in sales
                if sale.total_cad is not None and not sale.partial_award
            ),
            2,
        ),
        "detail_price_checks": len(detail_checks),
        "checks": [
            "source_row_count=3796",
            "delivered_sale_count=18",
            "delivered_only_sale_type",
            "no_duplicate_sale_ids",
            "non_negative_volume_and_price",
            "partial_award_rate_not_computed",
            "awarded_delivered_rows_have_price",
            "unsold_rows_have_no_price",
            "sale_year_matches_auction_end_year",
            "species_volume_reconciles_to_sale_volume",
            "summary_reconciles_to_export",
            "detail_prices_match_export",
        ],
    }


def build_manifest(
    args: argparse.Namespace,
    sales: list[Sale],
    species: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    validation: dict[str, Any],
    detail_meta: dict[str, dict[str, str]],
    detail_checks: list[dict[str, Any]],
    output_files: list[Path],
) -> dict[str, Any]:
    detail_pages = []
    for sale in sorted(sales, key=lambda item: item.sale_id):
        detail_path = args.details_dir / f"{sale.sale_id}.html"
        meta = detail_meta.get(sale.sale_id, {})
        entry: dict[str, Any] = {
            "sale_id": sale.sale_id,
            "url": sale.detail_url,
            "http_status": int(meta.get("http_status", "200")),
            "content_type": meta.get("content_type", "text/html; charset=utf-8"),
        }
        if detail_path.exists():
            entry["bytes"] = detail_path.stat().st_size
            entry["sha256"] = sha256_file(detail_path)
            entry["retrieved_utc"] = datetime.fromtimestamp(
                detail_path.stat().st_mtime, UTC
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        detail_pages.append(entry)

    pdf_entries = []
    for sale_id, meta in PDF_RESULTS.items():
        path = args.pdf_dir / meta["filename"]
        entry = {
            "sale_id": sale_id,
            "url": meta["url"],
            "http_status": meta["http_status"],
            "content_type": meta["content_type"],
            "bytes": meta["bytes"],
            "sha256": meta["sha256"],
            "retrieved_utc": meta["retrieved_utc"],
            "used_for_price": path.exists(),
        }
        pdf_entries.append(entry)

    outputs = []
    for path in output_files:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        outputs.append(
            {
                "file": path.name,
                "rows": len(rows),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "columns": list(rows[0].keys()) if rows else [],
            }
        )

    return {
        "dataset": "bmmb_delivered",
        "version": "0.1.0",
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/national/extract_bmmb_delivered.py",
        "source": {
            "id": "qc_bmmb_auction_archive",
            "name": "Bureau de mise en marché des bois - ventes archivées",
            "access": "public HTTP GET; no authentication; redirects to encheres.forets.gouv.qc.ca",
            "index_url": SOURCE_URL,
            "export": {
                "requested_url": EXPORT_URL,
                "final_url": EXPORT_FINAL_URL,
                "http_status": EXPORT_HTTP_STATUS,
                "content_type": EXPORT_CONTENT_TYPE,
                "content_disposition": EXPORT_CONTENT_DISPOSITION,
                "bytes": EXPORT_BYTES,
                "sha256": EXPORT_SHA256,
                "retrieved_utc": EXPORT_RETRIEVED_UTC,
                "sheet": SHEET_NAME,
                "data_rows": 3_796,
            },
            "archive_page": {
                "url": SOURCE_URL,
                "http_status": 200,
                "content_type": ARCHIVE_CONTENT_TYPE,
                "bytes": ARCHIVE_BYTES,
                "sha256": ARCHIVE_SHA256,
                "retrieved_utc": ARCHIVE_RETRIEVED_UTC,
            },
            "detail_pages": detail_pages,
            "result_pdfs": pdf_entries,
        },
        "filter": {
            "sale_type": DELIVERED,
            "excluded_sale_types": [RECOLTE, STANDING_MEASURED, STANDING_INVENTORY],
            "observed_price_rule": (
                "awarded sale with Prix de vente, or with a result PDF when "
                "the export price is blank"
            ),
        },
        "counts": validation,
        "outputs": outputs,
        "detail_price_cross_checks": detail_checks,
        "validation": {"status": "pass", **validation},
        "gaps": [
            "Only 18 delivered-to-destination sales exist in the archive; the most recent delivered sale ends in 2015.",
            "The sale price is a total lot value and is not published by species or grade; no allocation is made.",
            "The detail pages do not name the destination; the price point is delivered-to-destination, not a confirmed mill gate.",
            "The two 2012 result PDFs note that some offered volume was not awarded; no awarded volume is published, so a full rate is not computed and only a lower bound based on offered volume is reported.",
            "The sale type implies a delivered wood sale; the archive does not publish whether harvest, haul, or both are included, or the destination.",
            "Prices are nominal CAD for 2011, 2012, 2014, and 2015; no deflator is applied.",
            "BMMB auctioned volume is a sample of Crown volume, not the provincial harvest population.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument("--details-dir", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument("--detail-meta", type=Path, default=DEFAULT_DETAIL_META)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDFS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.export.exists():
        raise SystemExit(f"missing export: {args.export}")
    actual_export_hash = sha256_file(args.export)
    if actual_export_hash != EXPORT_SHA256:
        raise SystemExit(
            f"export sha256 {actual_export_hash} != expected {EXPORT_SHA256}; "
            "update provenance deliberately"
        )
    if args.export.stat().st_size != EXPORT_BYTES:
        raise SystemExit(
            f"export bytes {args.export.stat().st_size} != expected {EXPORT_BYTES}"
        )

    source_rows = read_export(args.export)
    detail_meta = load_detail_meta(args.detail_meta)
    detail_urls = {sale_id: meta["url"] for sale_id, meta in detail_meta.items()}
    pdf_prices, pdf_urls = load_pdf_prices(args.pdf_dir)

    parsed_sales = []
    for row in source_rows:
        sale_id = text(row["Numéro d'appel d'offres"])
        parsed_sales.append(
            parse_sale(
                row,
                detail_urls.get(sale_id, ""),
                pdf_prices,
                pdf_urls,
            )
        )

    delivered = [
        sale
        for sale in parsed_sales
        if sale.sale_type_family == "delivered_to_destination"
    ]
    if not delivered:
        raise SystemExit("no delivered sales found")

    detail_checks = (
        validate_detail_prices(delivered, args.details_dir)
        if args.details_dir.exists()
        else []
    )
    summary = summarise_sales(parsed_sales)
    species = species_rows(delivered)
    validation = validate(delivered, summary, source_rows, detail_checks)

    args.out.mkdir(parents=True, exist_ok=True)
    sales_path = args.out / "bmmb_delivered_sales.csv"
    species_path = args.out / "bmmb_delivered_species_volume.csv"
    summary_path = args.out / "bmmb_sale_type_summary.csv"
    write_csv(sales_path, SALE_COLUMNS, [sale_row(sale) for sale in delivered])
    write_csv(species_path, SPECIES_COLUMNS, species)
    write_csv(summary_path, SUMMARY_COLUMNS, summary)

    manifest = build_manifest(
        args,
        delivered,
        species,
        summary,
        validation,
        detail_meta,
        detail_checks,
        [sales_path, species_path, summary_path],
    )
    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
