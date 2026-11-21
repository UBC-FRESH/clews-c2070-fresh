#!/usr/bin/env python3
"""Extract BC Coast and Interior log-market report prices into a tidy dataset.

The public BC Timber Pricing Branch PDFs are average domestic log prices, not
transaction microdata.  This builder parses the published PDF text (via
``pdftotext -tsv``) and writes a standalone derived dataset.  It deliberately
does not touch the national forest cost/revenue ledger.

Usage
-----
    python scripts/national/build_bc_log_market.py \
        --raw-dir tmp/bc_log_market_raw \
        --out-dir data/derived/national/harvest_basis/bc_log_market
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

COAST_SPECIES = (
    "Alder",
    "Birch",
    "Cedar",
    "Cottonwood",
    "Cypress",
    "Fir",
    "Hembal",
    "Maple",
    "Pine",
    "Spruce",
)
INTERIOR_SPECIES = (
    "SPF",
    "Df-Larch",
    "Hem-Bal",
    "Cedar",
    "White Pine",
    "Yellow Pine",
    "Deciduous",
    "Other",
    "Total/Avg",
)
COAST_GRADES = set("BCDEFGHIJKLMNOPQRSTUVWXYZ")
MONTHS = {
    name.lower(): number for number, name in enumerate(calendar.month_name) if name
}
MONTHS.update(
    {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
)
PRODUCT_LABELS = {
    "sawlog": "sawlog",
    "peelers": "peelers",
    "poles / house": "poles_house",
    "poles/house": "poles_house",
    "minor products": "minor_products",
    "pulpwood": "pulpwood",
    "other": "other",
    "total": "total",
    "wtd. average": "weighted_average",
    "weighted average": "weighted_average",
}
PRICE_BASIS = "mill_delivered"
OBSERVATION_TYPE = "reported_market_average"
ARTIFACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class Word:
    page: int
    left: float
    top: float
    right: float
    text: str


@dataclass
class Row:
    page: int
    top: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass
class ReportMeta:
    report_id: str
    region: str
    report_basis: str
    log_class: str
    period_start: date
    period_end: date
    filename: str
    url: str
    sha256: str
    retrieved_utc: str


class ParseError(RuntimeError):
    """Raised when a PDF does not match the expected published layout."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_tsv_words(path: Path) -> list[Word]:
    words: list[Word] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["level"] != "5":
                continue
            left = float(row["left"])
            words.append(
                Word(
                    page=int(row["page_num"]),
                    left=left,
                    top=float(row["top"]),
                    right=left + float(row["width"]),
                    text=row["text"].strip(),
                )
            )
    if not words:
        raise ParseError(f"no text words in {path}")
    return words


def group_rows(words: Sequence[Word], tolerance: float = 6.0) -> list[Row]:
    """Cluster words into visual text rows by baseline."""
    rows: list[Row] = []
    for word in sorted(words, key=lambda item: (item.page, item.top, item.left)):
        if (
            rows
            and rows[-1].page == word.page
            and abs(rows[-1].top - word.top) <= tolerance
        ):
            rows[-1].words.append(word)
            continue
        rows.append(Row(page=word.page, top=word.top, words=[word]))
    for row in rows:
        row.words.sort(key=lambda item: item.left)
    return rows


def parse_number(raw: str) -> float | None:
    text = raw.strip().replace("$", "").replace(",", "").replace("%", "")
    if text in {"", "-", "–", "—", "n/a", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ParseError(f"not a number: {raw!r}") from exc


def parse_month_year(text: str) -> tuple[int, int]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    match = re.search(r"([A-Za-z]+)\s*,?\s*(?:\d{1,2},?\s*)?(\d{4})", cleaned)
    if not match:
        raise ParseError(f"cannot find month/year in {text!r}")
    month_name, year = match.group(1).lower(), int(match.group(2))
    if month_name not in MONTHS:
        raise ParseError(f"unknown month {month_name!r} in {text!r}")
    return int(MONTHS[month_name]), year


def month_end(month: int, year: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_coast_period(text: str) -> tuple[date, date, str]:
    match = re.search(r"(1|3)\s*month period ending:\s*(.+)", text, re.IGNORECASE)
    if not match:
        raise ParseError(f"coast period not found in {text!r}")
    basis = f"{match.group(1)}_month"
    raw = match.group(2).strip()
    month, year = parse_month_year(raw)
    end = month_end(month, year)
    if basis == "1_month":
        start = end.replace(day=1)
    else:
        start_month = month - 2
        start_year = year
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        start = date(start_year, start_month, 1)
    return start, end, basis


def parse_interior_period(text: str) -> tuple[date, date, str]:
    flat = re.sub(r"\s+", " ", text)
    month_match = re.search(r"Report for the month of ([A-Za-z]+) (\d{4})", flat)
    if month_match:
        month = int(MONTHS[month_match.group(1).lower()])
        year = int(month_match.group(2))
        end = month_end(month, year)
        return end.replace(day=1), end, "1_month"
    range_match = re.search(
        r"Report for the 3 month period of ([A-Za-z]+ \d{1,2}, \d{4}) to "
        r"([A-Za-z]+ \d{1,2}, \d{4})",
        flat,
    )
    if not range_match:
        raise ParseError(f"interior period not found in {text!r}")
    start = datetime.strptime(range_match.group(1), "%B %d, %Y").date()
    end = datetime.strptime(range_match.group(2), "%B %d, %Y").date()
    return start, end, "3_month"


def classify_filename(filename: str) -> tuple[str, str, str]:
    match = re.match(r"^(1m|3m)(c|2gc|i)_", filename, re.IGNORECASE)
    if not match:
        raise ParseError(f"unrecognised log-market filename {filename!r}")
    horizon, series = match.group(1).lower(), match.group(2).lower()
    basis = "1_month" if horizon == "1m" else "3_month"
    if series == "i":
        return "interior", basis, "not_reported"
    log_class = "second_growth" if series == "2gc" else "old_growth"
    return "coast", basis, log_class


def extract_title(text: str) -> str:
    for line in text.splitlines():
        if "Average Log Prices" in line or "B.C. Interior Log Market" in line:
            return line.strip()
    raise ParseError("report title not found")


def build_column_map(
    header: Row,
    species: Sequence[str],
    *,
    multiword: bool = False,
) -> list[tuple[str, float]]:
    """Return (species, left-edge) pairs from a table header row."""
    if not multiword:
        found = {word.text: word.left for word in header.words if word.text in species}
        missing = [name for name in species if name not in found]
        if missing:
            raise ParseError(f"missing species headers: {missing}")
        return [(name, found[name]) for name in species]

    texts = [(word.left, word.text) for word in header.words]
    positions: dict[str, float] = {}
    for index, (left, text) in enumerate(texts):
        if text in {"SPF", "Df-Larch", "Hem-Bal", "Cedar", "Deciduous", "Other"}:
            positions[text] = left
        elif (
            text == "White" and index + 1 < len(texts) and texts[index + 1][1] == "Pine"
        ):
            positions["White Pine"] = left
        elif (
            text == "Yellow"
            and index + 1 < len(texts)
            and texts[index + 1][1] == "Pine"
        ):
            positions["Yellow Pine"] = left
        elif text == "Total/Avg":
            positions["Total/Avg"] = left
    missing = [name for name in species if name not in positions]
    if missing:
        raise ParseError(f"missing species headers: {missing}")
    return [(name, positions[name]) for name in species]


def assign_by_right_edge(
    value: Word,
    columns: Sequence[tuple[str, float]],
) -> str:
    """Assign a right-aligned numeric cell to its header-left interval."""
    if value.right < columns[0][1] - 8:
        raise ParseError(f"value {value.text!r} left of table")
    selected = columns[0][0]
    for name, left in columns:
        if value.right >= left - 8:
            selected = name
        else:
            break
    return selected


def numeric_words(row: Row, minimum_left: float) -> list[Word]:
    words: list[Word] = []
    for word in row.words:
        if word.left < minimum_left:
            continue
        try:
            parse_number(word.text)
        except ParseError:
            continue
        words.append(word)
    return words


def find_row(
    rows: Sequence[Row], y: float, text: str, max_dy: float = 6.0
) -> Row | None:
    for row in rows:
        if abs(row.top - y) <= max_dy and text in row.text:
            return row
    return None


def validate_coast_totals(totals: dict[str, object]) -> dict[str, object]:
    volume = totals.get("report_total_volume_m3")
    value = totals.get("report_total_value_cad")
    avg = totals.get("report_avg_price_cad_m3")
    reported = any(item is not None for item in (volume, value, avg))
    if not reported:
        return totals
    if volume is None:
        return {
            **totals,
            "report_total_volume_m3": None,
            "report_total_value_cad": None,
            "report_avg_price_cad_m3": None,
            "report_totals_status": "not_reported_or_unparseable",
        }
    if avg is not None and not 0 < float(avg) <= 2000:
        return {
            **totals,
            "report_total_volume_m3": None,
            "report_total_value_cad": None,
            "report_avg_price_cad_m3": None,
            "report_totals_status": "not_reported_or_unparseable",
        }
    if value is not None and avg is not None:
        expected = float(volume) * float(avg)
        if abs(float(value) - expected) / max(float(value), 1.0) > 0.02:
            return {
                **totals,
                "report_total_volume_m3": None,
                "report_total_value_cad": None,
                "report_avg_price_cad_m3": None,
                "report_totals_status": "inconsistent",
            }
    return {**totals, "report_totals_status": "consistent"}


def parse_coast(
    rows: Sequence[Row],
    text: str,
    meta: ReportMeta,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    header = next(
        (
            row
            for row in rows
            if "Grade" in row.text
            and sum(word.text in COAST_SPECIES for word in row.words) >= 5
        ),
        None,
    )
    if header is None:
        raise ParseError(f"{meta.filename}: coast header not found")
    columns = build_column_map(header, COAST_SPECIES)
    table_left = columns[0][1]

    volume_row = next(
        (
            row
            for row in rows
            if "Volume(m3)" in row.text
            or "Volume (m3)" in row.text
            or "Species %" in row.text
            or "Total volume" in row.text
        ),
        None,
    )
    if volume_row is None:
        raise ParseError(f"{meta.filename}: coast table end row not found")
    table_rows = [row for row in rows if header.top < row.top < volume_row.top + 3]

    observations: list[dict[str, object]] = []
    grades_seen: set[str] = set()
    current_grade: str | None = None
    for row in table_rows:
        grade_letters = [
            word.text
            for word in row.words
            if word.left < table_left - 80
            and len(word.text) == 1
            and word.text in COAST_GRADES
        ]
        if grade_letters:
            current_grade = grade_letters[0]
            grades_seen.add(current_grade)
        if "AMV" not in row.text and "%" not in row.text:
            continue
        if current_grade is None:
            raise ParseError(
                f"{meta.filename}: values before grade label at y={row.top}"
            )
        role = "price" if "AMV" in row.text else "volume_share"
        values = numeric_words(row, table_left - 8)
        seen: set[str] = set()
        for value in values:
            species = assign_by_right_edge(value, columns)
            if species in seen:
                raise ParseError(
                    f"{meta.filename}: duplicate {species} on row {row.text!r}"
                )
            seen.add(species)
            observations.append(
                {
                    "grade": current_grade,
                    "species": species,
                    "role": role,
                    "value": parse_number(value.text),
                }
            )

    by_cell: dict[tuple[str, str], dict[str, object]] = {}
    for obs in observations:
        key = (str(obs["grade"]), str(obs["species"]))
        cell = by_cell.setdefault(
            key,
            {
                "grade": obs["grade"],
                "species": obs["species"],
                "price_cad_m3": None,
                "volume_share_pct": None,
            },
        )
        if obs["role"] == "price":
            cell["price_cad_m3"] = obs["value"]
        else:
            cell["volume_share_pct"] = obs["value"]

    result = [cell for cell in by_cell.values() if cell["price_cad_m3"] is not None]

    totals: dict[str, object] = {}
    for row in rows:
        if row.top <= volume_row.top:
            continue
        label = row.text.strip()
        if label.startswith("Total volume (m3)"):
            numbers = numeric_words(row, table_left - 20)
            totals["report_total_volume_m3"] = (
                parse_number(numbers[-1].text) if numbers else None
            )
        elif label.startswith("Total value $"):
            numbers = numeric_words(row, table_left - 20)
            totals["report_total_value_cad"] = (
                parse_number(numbers[-1].text) if numbers else None
            )
        elif label.startswith("$/m3 "):
            numbers = numeric_words(row, table_left - 20)
            totals["report_avg_price_cad_m3"] = (
                parse_number(numbers[-1].text) if numbers else None
            )
    totals["table_cells_possible"] = len(grades_seen) * len(COAST_SPECIES)
    totals["grade_or_product_columns"] = len(grades_seen)
    totals["species_columns"] = len(COAST_SPECIES)
    return result, validate_coast_totals(totals)


def parse_interior(
    rows: Sequence[Row],
    text: str,
    meta: ReportMeta,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    header = next(
        (
            row
            for row in rows
            if "SPF" in row.text and "Df-Larch" in row.text and "Hem-Bal" in row.text
        ),
        None,
    )
    if header is None:
        raise ParseError(f"{meta.filename}: interior header not found")
    columns = build_column_map(header, INTERIOR_SPECIES, multiword=True)
    columns = [column for column in columns if column[0] != "Total/Avg"]
    table_left = columns[0][1]
    table_right = next(
        (word.left for word in header.words if word.text == "Total/Avg"),
        header.words[-1].right,
    )

    volume_anchor = next(
        (row for row in rows if "Volume" in row.text and row.top > header.top), None
    )
    price_anchor = next(
        (
            row
            for row in rows
            if ("Average" in row.text and "Price" in row.text) or "($/m³)" in row.text
        ),
        None,
    )
    if volume_anchor is None or price_anchor is None:
        raise ParseError(f"{meta.filename}: interior block anchors not found")

    observations: dict[tuple[str, str], dict[str, object]] = {}
    products_seen: set[str] = set()
    block = None
    for row in rows:
        if row.top <= header.top:
            continue
        if "Prepared by" in row.text or "Intended end use" in row.text:
            break
        if row is volume_anchor:
            block = "volume"
        elif row is price_anchor:
            block = "price"
        if block is None:
            continue
        label_words = [word for word in row.words if word.left < table_left - 5]
        label = re.sub(r"\s+", " ", " ".join(word.text for word in label_words)).strip()
        label = re.sub(r"\s+\d+$", "", label)
        product = PRODUCT_LABELS.get(label.lower())
        if product is None:
            lowered = label.lower()
            if "sawlog" in lowered:
                product = "sawlog"
            elif "peeler" in lowered:
                product = "peelers"
            elif "poles" in lowered and "house" in lowered:
                product = "poles_house"
            elif "minor" in lowered and "product" in lowered:
                product = "minor_products"
            elif "pulpwood" in lowered:
                product = "pulpwood"
            elif lowered.startswith("other"):
                product = "other"
            elif lowered.startswith("total"):
                product = "total"
            elif "wtd" in lowered or "weighted" in lowered:
                product = "weighted_average"
        if product is None:
            continue
        if product == "total":
            continue
        products_seen.add(product)
        values = numeric_words(row, table_left - 8)
        for value in values:
            if value.right >= table_right - 8:
                if product == "weighted_average" and block == "price":
                    totals_key = "report_avg_price_cad_m3"
                    observations.setdefault(
                        ("weighted_average", "ALL"),
                        {
                            "end_use": "weighted_average",
                            "species": "ALL",
                            "price_cad_m3": None,
                            "volume_m3": None,
                        },
                    )
                    observations[("weighted_average", "ALL")][totals_key] = (
                        parse_number(value.text)
                    )
                continue
            species = assign_by_right_edge(value, columns)
            key = (product, species)
            cell = observations.setdefault(
                key,
                {
                    "end_use": product,
                    "species": species,
                    "price_cad_m3": None,
                    "volume_m3": None,
                },
            )
            number = parse_number(value.text)
            if number is None:
                continue
            if block == "volume":
                cell["volume_m3"] = number
            else:
                cell["price_cad_m3"] = number

    totals: dict[str, object] = {}
    for row in rows:
        if "Total" not in row.text:
            continue
        if not volume_anchor.top < row.top < price_anchor.top:
            continue
        label_words = [word for word in row.words if word.left < table_left - 5]
        label = " ".join(word.text for word in label_words).strip().lower()
        if not label.startswith("total"):
            continue
        values = numeric_words(row, table_left)
        totals["report_total_volume_m3"] = (
            parse_number(values[-1].text) if values else None
        )
    for row in rows:
        if "Wtd" not in row.text and "Weighted" not in row.text:
            continue
        values = numeric_words(row, table_left)
        totals["report_avg_price_cad_m3"] = (
            parse_number(values[-1].text) if values else None
        )

    result = [
        cell
        for cell in observations.values()
        if cell["price_cad_m3"] is not None or cell["volume_m3"] is not None
    ]
    totals["table_cells_possible"] = len(products_seen) * len(columns)
    totals["grade_or_product_columns"] = len(products_seen)
    totals["species_columns"] = len(columns)
    totals["report_totals_status"] = (
        "reported"
        if totals.get("report_avg_price_cad_m3") is not None
        else "not_reported"
    )
    return result, totals


def load_source_map(raw_dir: Path) -> dict[str, dict[str, str]]:
    source_map: dict[str, dict[str, str]] = {}
    for name in ("download_log.json", "extra_sources.json"):
        path = raw_dir / name
        if not path.exists():
            continue
        for entry in json.loads(path.read_text(encoding="utf-8")):
            filename = Path(entry["file"]).name
            source_map[filename] = entry
    return source_map


def read_pdfinfo_pages(pdf_path: Path) -> int | None:
    try:
        output = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"^Pages:\s+(\d+)", output, re.MULTILINE)
    return int(match.group(1)) if match else None


def parse_report(
    tsv_path: Path,
    pdf_path: Path,
    source: dict[str, str],
) -> tuple[ReportMeta, list[dict[str, object]], dict[str, object]]:
    filename = pdf_path.stem
    region, basis, log_class = classify_filename(filename)
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if region == "coast":
        period_start, period_end, parsed_basis = parse_coast_period(text)
    else:
        period_start, period_end, parsed_basis = parse_interior_period(text)
    if parsed_basis != basis:
        raise ParseError(
            f"{filename}: filename basis {basis} != document {parsed_basis}"
        )
    meta = ReportMeta(
        report_id=f"{region}_{basis}_{log_class}_{period_end:%Y-%m}",
        region=region,
        report_basis=basis,
        log_class=log_class,
        period_start=period_start,
        period_end=period_end,
        filename=filename,
        url=source.get("url", ""),
        sha256=source.get("sha256") or sha256_file(pdf_path),
        retrieved_utc=source.get("retrieved_utc", ""),
    )
    rows = group_rows(read_tsv_words(tsv_path))
    if region == "coast":
        cells, totals = parse_coast(rows, text, meta)
    else:
        cells, totals = parse_interior(rows, text, meta)
    return meta, cells, totals


def write_csv(
    path: Path, rows: Sequence[dict[str, object]], fieldnames: Sequence[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("tmp/bc_log_market_raw"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/derived/national/harvest_basis/bc_log_market"),
    )
    args = parser.parse_args()

    source_map = load_source_map(args.raw_dir)
    pdf_paths = sorted((args.raw_dir / "pdfs").glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"no PDFs in {args.raw_dir / 'pdfs'}")

    price_rows: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    source_records: list[dict[str, object]] = []

    for pdf_path in pdf_paths:
        filename = pdf_path.stem
        try:
            region, _, _ = classify_filename(filename)
        except ParseError:
            continue
        tsv_path = args.raw_dir / "tsv" / f"{filename}.tsv"
        if not tsv_path.exists():
            failures.append({"file": filename, "error": "missing_tsv"})
            continue
        source = source_map.get(pdf_path.name, {})
        try:
            meta, cells, totals = parse_report(tsv_path, pdf_path, source)
        except (ParseError, subprocess.CalledProcessError) as exc:
            failures.append({"file": filename, "error": str(exc)})
            continue

        priced = 0
        volume_reported = 0
        for cell in cells:
            price_value = cell.get("price_cad_m3")
            volume_value = cell.get("volume_m3")
            price_status = "reported" if price_value is not None else "blank_cell"
            volume_status = "reported" if volume_value is not None else "blank_cell"
            if region == "coast":
                volume_status = "not_reported_by_grade"
            if price_value is not None:
                priced += 1
            if volume_value is not None:
                volume_reported += 1
            if price_value is None:
                plausibility = "not_priced"
            elif price_value == 0:
                plausibility = "zero_cad_m3"
            elif price_value < 1:
                plausibility = "below_1_cad_m3"
            elif price_value > 1000:
                plausibility = "above_1000_cad_m3"
            else:
                plausibility = "ok"
            price_rows.append(
                {
                    "report_id": meta.report_id,
                    "region": meta.region,
                    "report_basis": meta.report_basis,
                    "period_start": meta.period_start.isoformat(),
                    "period_end": meta.period_end.isoformat(),
                    "log_class": meta.log_class,
                    "species": cell["species"],
                    "grade": cell.get("grade", ""),
                    "end_use": cell.get("end_use", ""),
                    "price_cad_m3": price_value,
                    "price_basis": PRICE_BASIS,
                    "observation_type": OBSERVATION_TYPE,
                    "is_aggregate": bool(
                        meta.region == "interior"
                        and cell.get("end_use") == "weighted_average"
                    ),
                    "price_status": price_status,
                    "price_plausibility": plausibility,
                    "volume_m3": volume_value,
                    "volume_status": volume_status,
                    "volume_share_pct": cell.get("volume_share_pct"),
                    "volume_share_status": (
                        "reported"
                        if cell.get("volume_share_pct") is not None
                        else "blank_cell"
                    ),
                    "report_total_volume_m3": totals.get("report_total_volume_m3"),
                    "report_total_value_cad": totals.get("report_total_value_cad"),
                    "report_avg_price_cad_m3": totals.get("report_avg_price_cad_m3"),
                    "report_totals_status": totals.get(
                        "report_totals_status",
                        "reported"
                        if totals.get("report_avg_price_cad_m3") is not None
                        else "not_reported",
                    ),
                    "source_document": pdf_path.name,
                    "source_url": meta.url,
                    "source_sha256": meta.sha256,
                    "retrieved_utc": meta.retrieved_utc,
                }
            )
        coverage_rows.append(
            {
                "report_id": meta.report_id,
                "region": meta.region,
                "report_basis": meta.report_basis,
                "log_class": meta.log_class,
                "period_start": meta.period_start.isoformat(),
                "period_end": meta.period_end.isoformat(),
                "source_document": pdf_path.name,
                "source_url": meta.url,
                "source_sha256": meta.sha256,
                "retrieved_utc": meta.retrieved_utc,
                "pages": read_pdfinfo_pages(pdf_path),
                "species_columns": totals.get("species_columns"),
                "grade_or_product_columns": totals.get("grade_or_product_columns"),
                "table_cells_possible": totals.get("table_cells_possible"),
                "cells_emitted": len(cells),
                "price_cells_reported": priced,
                "price_cells_blank": max(
                    0,
                    int(totals.get("table_cells_possible") or 0) - priced,
                ),
                "volume_cells_reported": volume_reported,
                "parse_status": "ok",
                "report_total_volume_m3": totals.get("report_total_volume_m3"),
                "report_total_value_cad": totals.get("report_total_value_cad"),
                "report_avg_price_cad_m3": totals.get("report_avg_price_cad_m3"),
                "report_totals_status": totals.get(
                    "report_totals_status",
                    "reported"
                    if totals.get("report_avg_price_cad_m3") is not None
                    else "not_reported",
                ),
            }
        )
        source_records.append(
            {
                "file": pdf_path.name,
                "url": meta.url,
                "retrieved_utc": meta.retrieved_utc,
                "sha256": meta.sha256,
                "bytes": pdf_path.stat().st_size,
                "region": meta.region,
                "report_basis": meta.report_basis,
                "log_class": meta.log_class,
                "period_start": meta.period_start.isoformat(),
                "period_end": meta.period_end.isoformat(),
            }
        )

    price_rows.sort(
        key=lambda row: (
            str(row["report_id"]),
            str(row["species"]),
            str(row["grade"]),
            str(row["end_use"]),
        )
    )
    coverage_rows.sort(key=lambda row: str(row["report_id"]))

    keys = [
        (row["report_id"], row["species"], row["grade"], row["end_use"])
        for row in price_rows
    ]
    if len(keys) != len(set(keys)):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        raise SystemExit(f"duplicate observation keys: {duplicates[:5]}")

    bad_prices = [
        row
        for row in price_rows
        if row["price_cad_m3"] is not None
        and (
            not isinstance(row["price_cad_m3"], (int, float))
            or row["price_cad_m3"] != row["price_cad_m3"]
            or row["price_cad_m3"] < 0
            or row["price_cad_m3"] > 2000
        )
    ]
    if bad_prices:
        raise SystemExit(f"invalid prices: {bad_prices[:3]}")

    out_dir = args.out_dir
    price_fields = [
        "report_id",
        "region",
        "report_basis",
        "period_start",
        "period_end",
        "log_class",
        "species",
        "grade",
        "end_use",
        "price_cad_m3",
        "price_basis",
        "observation_type",
        "is_aggregate",
        "price_status",
        "price_plausibility",
        "volume_m3",
        "volume_status",
        "volume_share_pct",
        "volume_share_status",
        "report_total_volume_m3",
        "report_total_value_cad",
        "report_avg_price_cad_m3",
        "report_totals_status",
        "source_document",
        "source_url",
        "source_sha256",
        "retrieved_utc",
    ]
    coverage_fields = [
        "report_id",
        "region",
        "report_basis",
        "log_class",
        "period_start",
        "period_end",
        "source_document",
        "source_url",
        "source_sha256",
        "retrieved_utc",
        "pages",
        "species_columns",
        "grade_or_product_columns",
        "table_cells_possible",
        "cells_emitted",
        "price_cells_reported",
        "price_cells_blank",
        "volume_cells_reported",
        "parse_status",
        "report_total_volume_m3",
        "report_total_value_cad",
        "report_avg_price_cad_m3",
        "report_totals_status",
    ]
    write_csv(out_dir / "bc_log_market_prices.csv", price_rows, price_fields)
    write_csv(out_dir / "bc_log_market_coverage.csv", coverage_rows, coverage_fields)
    (out_dir / "bc_log_market_sources.json").write_text(
        json.dumps(source_records, indent=1) + "\n", encoding="utf-8"
    )

    discovery_pages: list[dict[str, object]] = []
    page_urls = {
        "coast_page.html": "https://www2.gov.bc.ca/gov/content/industry/forestry/competitive-forest-industry/timber-pricing/coast-timber-pricing/coast-log-market-reports",
        "interior_page.html": "https://www2.gov.bc.ca/gov/content/industry/forestry/competitive-forest-industry/timber-pricing/interior-timber-pricing/interior-log-market-reports",
    }
    for year in range(2019, 2026):
        page_urls[f"coast_{year}.html"] = (
            "https://www2.gov.bc.ca/gov/content/industry/forestry/competitive-forest-industry/"
            f"timber-pricing/coast-timber-pricing/coast-log-market-reports/{year}"
        )
        page_urls[f"interior_{year}.html"] = (
            "https://www2.gov.bc.ca/gov/content/industry/forestry/competitive-forest-industry/"
            f"timber-pricing/interior-timber-pricing/interior-log-market-reports/{year}"
        )
    for name, url in page_urls.items():
        path = args.raw_dir / name
        if not path.exists():
            path = args.raw_dir / "pages" / name
        if not path.exists():
            continue
        discovery_pages.append(
            {
                "file": name,
                "url": url,
                "sha256": sha256_file(path),
                "retrieved_utc": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=UTC
                ).isoformat(timespec="seconds"),
            }
        )

    summary = {
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "scope": "BC Coast and Interior public log-market average prices, 2019-2026",
        "price_basis": PRICE_BASIS,
        "observation_type": OBSERVATION_TYPE,
        "discovery": {
            "index_and_archive_pages": discovery_pages,
            "method": "Public HTML index and yearly historical-version pages; PDF links followed without authentication.",
            "restricted_sources_excluded": [
                "ILCR microdata",
                "CCTMCS microdata",
            ],
        },
        "reports_parsed": len(coverage_rows),
        "reports_failed": failures,
        "rows": len(price_rows),
        "reports_by_region": dict(
            sorted(
                (region, sum(1 for row in coverage_rows if row["region"] == region))
                for region in {row["region"] for row in coverage_rows}
            )
        ),
        "reports_by_region_basis": {
            f"{region}_{basis}": sum(
                1
                for row in coverage_rows
                if row["region"] == region and row["report_basis"] == basis
            )
            for region in {row["region"] for row in coverage_rows}
            for basis in {row["report_basis"] for row in coverage_rows}
        },
        "rows_by_region": dict(
            sorted(
                (region, sum(1 for row in price_rows if row["region"] == region))
                for region in {row["region"] for row in price_rows}
            )
        ),
        "price_range_cad_m3": {
            "min": min(
                row["price_cad_m3"]
                for row in price_rows
                if row["price_cad_m3"] is not None
            ),
            "max": max(
                row["price_cad_m3"]
                for row in price_rows
                if row["price_cad_m3"] is not None
            ),
        },
        "validation": {
            "negative_or_nan_prices": 0,
            "duplicate_keys": 0,
            "hard_price_range_check_cad_m3": [0, 2000],
            "flags": {
                "price_below_1_cad_m3": sum(
                    1
                    for row in price_rows
                    if row["price_cad_m3"] is not None and row["price_cad_m3"] < 1
                ),
                "price_above_1000_cad_m3": sum(
                    1
                    for row in price_rows
                    if row["price_cad_m3"] is not None and row["price_cad_m3"] > 1000
                ),
            },
        },
        "inputs": source_records,
        "outputs": [
            {
                "path": "bc_log_market_prices.csv",
                "rows": len(price_rows),
                "sha256": sha256_file(out_dir / "bc_log_market_prices.csv"),
            },
            {
                "path": "bc_log_market_coverage.csv",
                "rows": len(coverage_rows),
                "sha256": sha256_file(out_dir / "bc_log_market_coverage.csv"),
            },
        ],
        "assumptions": [
            "Prices are published averages (AMV) for domestic log sales, not transaction-level records.",
            "Coast old-growth reports blend old and mixed growth transactions per ministry policy.",
            "Coast grade-level volume is not published; only a species volume total is published, and only for some species.",
            "Interior volume is reported by product and species group; '-' means no reported volume or price, not zero.",
            "Report period end is the published month end; three-month reports use the published exact date range.",
            "No price or volume is interpolated, imputed, or carried forward.",
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                k: summary[k]
                for k in ("reports_parsed", "rows", "reports_by_region_basis")
            },
            indent=1,
        )
    )
    if failures:
        print(f"FAILURES: {len(failures)}")
        for failure in failures[:20]:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
