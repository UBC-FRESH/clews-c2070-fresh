#!/usr/bin/env python3
"""Extract BC public appraisal cost allowances into a standalone dataset.

The Interior Appraisal Manual (IAM), Coast Appraisal Manual (CAM), and the
quarterly Interior Basic Silviculture Costs sheet publish *allowances* used in
stumpage appraisals.  They are not observed contractor costs and must not be
merged into the forest cost/revenue ledger.

The builder reads the public PDFs, parses the published tables, validates the
results against fixed spot checks, and writes:

    bc_appraisal_costs.csv
    bc_appraisal_coverage.csv
    manifest.json

Usage
-----
    python scripts/national/build_bc_appraisal_costs.py \
        --pdf-dir tmp/bc_appraisal_costs_raw/pdfs \
        --out-dir data/derived/national/harvest_basis/bc_appraisal_costs
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDF_DIR = ROOT / "tmp/bc_appraisal_costs_raw/pdfs"
DEFAULT_OUT_DIR = (
    ROOT / "data/derived/national/harvest_basis/bc_appraisal_costs"
)
OBSERVATION_TYPE = "appraisal_allowance"
ARTIFACT_VERSION = "1.0.0"

IAM_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/interior-timber-pricing/interior-appraisal-manual/"
    "iam_2026_master_b.pdf"
)
CAM_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/coast-timber-pricing/coast-appraisal-manual/"
    "cam_2026_master.pdf"
)
SILV_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/interior-timber-pricing/"
    "basic_silv_costs_oct_26.pdf"
)
SILV_JUL_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/interior-timber-pricing/"
    "basic_silv_costs_jul_26.pdf"
)


class ParseError(RuntimeError):
    """Raised when a published table does not match its expected layout."""


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    filename: str
    url: str
    effective_date: str
    retrieved_utc: str
    sha256: str
    pages: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def page_count(path: Path) -> int:
    result = subprocess.run(
        ["pdfinfo", str(path)], check=True, capture_output=True, text=True
    )
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
    if not match:
        raise ParseError(f"no page count for {path}")
    return int(match.group(1))


def number(raw: str) -> float:
    text = raw.strip().replace("$", "").replace(",", "").replace("+", "")
    if not text:
        raise ParseError(f"empty number: {raw!r}")
    try:
        return float(text)
    except ValueError as exc:
        raise ParseError(f"not a number: {raw!r}") from exc


def optional_number(raw: str) -> float | None:
    text = raw.strip()
    if text in {"", "-", "n/a", "N/A"}:
        return None
    return number(text)


def clean(raw: str | None) -> str:
    return re.sub(r"\s+", " ", raw or "").strip()


def numbers_in(text: str) -> list[float]:
    return [number(token) for token in re.findall(r"[\d,]+(?:\.\d+)?", text)]


def flatten(row: Sequence[str | None]) -> list[str]:
    return [clean(cell) for cell in row if clean(cell)]


def read_pages(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def read_tables(pdf_path: Path, page_number: int) -> list[list[list[str | None]]]:
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page_number - 1].extract_tables()


def add_row(
    rows: list[dict[str, object]],
    *,
    source: Source,
    region: str,
    component_group: str,
    component: str,
    subcomponent: str,
    value: float,
    cost_basis: str,
    table_reference: str,
    source_page: int,
    zone_basis: str = "",
    zone: str = "",
    road_group: str = "",
    species: str = "",
    operability: str = "",
    matrix_row: str = "",
    matrix_column: str = "",
    min_value: float | None = None,
    max_value: float | None = None,
    unit_note: str = "",
    notes: str = "",
) -> None:
    if not math.isfinite(value) or value < 0:
        raise ParseError(f"invalid {component} value: {value}")
    for bound in (min_value, max_value):
        if bound is not None and (not math.isfinite(bound) or bound < 0):
            raise ParseError(f"invalid {component} bound: {bound}")
    rows.append(
        {
            "record_id": "",
            "jurisdiction": "BC",
            "region": region,
            "source_series": source.label,
            "component_group": component_group,
            "component": component,
            "subcomponent": subcomponent,
            "species": species,
            "operability": operability,
            "zone_basis": zone_basis,
            "zone": zone,
            "road_group": road_group,
            "effective_date": source.effective_date,
            "year": int(source.effective_date[:4]),
            "cost_basis": cost_basis,
            "value": round(value, 6),
            "min_value": None if min_value is None else round(min_value, 6),
            "max_value": None if max_value is None else round(max_value, 6),
            "matrix_row": matrix_row,
            "matrix_column": matrix_column,
            "unit_note": unit_note,
            "table_reference": table_reference,
            "source_page": source_page,
            "source_document": source.filename,
            "source_url": source.url,
            "source_sha256": source.sha256,
            "observation_type": OBSERVATION_TYPE,
            "notes": notes,
        }
    )


# ---------------------------------------------------------------------------
# Interior Basic Silviculture Costs (quarterly, forest zones)


def parse_basic_silviculture(
    text: str,
    source: Source,
    rows: list[dict[str, object]],
) -> int:
    zones: dict[str, float] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z ]*?Zone)\s+([\d.]+)\s*$", line)
        if not match:
            continue
        zone, raw_value = clean(match.group(1)), match.group(2)
        if zone in {"Forest Zone", "All Species Average"}:
            continue
        zones[zone] = number(raw_value)
    expected = {
        "North Central Zone",
        "North East Zone",
        "North West Zone",
        "North Fort Nelson Zone",
        "South Central Zone",
        "South East Zone",
        "South West Zone",
    }
    if set(zones) != expected:
        raise ParseError(f"unexpected silviculture zones: {sorted(zones)}")
    for zone, value in zones.items():
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="silviculture",
            component="basic_silviculture",
            subcomponent="all_species_average",
            zone_basis="forest_zone",
            zone=zone,
            species="all",
            value=value,
            cost_basis="CAD/m3",
            table_reference="Interior Basic Silviculture Costs",
            source_page=1,
            notes="Quarterly basic silviculture allowance; published by forest zone.",
        )
    return len(zones)


# ---------------------------------------------------------------------------
# IAM tables


def parse_iam_fma(
    tables: Sequence[Sequence[Sequence[str | None]]],
    source: Source,
    rows: list[dict[str, object]],
) -> int:
    table = next(
        (item for item in tables if any("Constant" in flatten(row) for row in item)),
        None,
    )
    if table is None:
        raise ParseError("IAM Table 4-1 not found")
    count = 0
    for raw in table:
        cells = flatten(raw)
        if len(cells) != 4:
            continue
        region, constant, minimum, maximum = cells
        if not all(re.fullmatch(r"[\d,]+(?:\.\d+)?", item) for item in (constant, minimum, maximum)):
            continue
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="tenure_obligation",
            component="forest_management_administration_constant",
            subcomponent="regional_constant",
            zone_basis="appraisal_region",
            zone=region,
            value=number(constant),
            min_value=number(minimum),
            max_value=number(maximum),
            cost_basis="CAD/m3",
            table_reference="IAM Table 4-1",
            source_page=69,
            unit_note="constant with published min/max clamp",
            notes="FMA equation constant; clamp to min/max after equation.",
        )
        count += 1
    if count != 6:
        raise ParseError(f"IAM Table 4-1 expected 6 regions, got {count}")
    return count


def parse_iam_culverts(
    tables: Sequence[Sequence[Sequence[str | None]]],
    source: Source,
    rows: list[dict[str, object]],
) -> int:
    table = next(
        (item for item in tables if item and "PART A" in clean(item[0][0])),
        None,
    )
    if table is None:
        raise ParseError("IAM Table 4-3 not found")
    diameters = [
        clean(cell) for cell in table[2][1:] if clean(cell)
    ]
    if len(diameters) != 14:
        raise ParseError(f"IAM culvert diameters: {diameters}")
    count = 0
    part_b = False
    for raw in table[3:]:
        cells = flatten(raw)
        if not cells:
            continue
        if any("PART B" in cell for cell in cells):
            part_b = True
            continue
        if part_b:
            values = [cell for cell in cells if cell != "avg./m"]
            if len(values) != 14:
                continue
            for diameter, value in zip(diameters, values, strict=True):
                add_row(
                    rows,
                    source=source,
                    region="Interior",
                    component_group="culvert",
                    component="culvert_supply_delivery",
                    subcomponent="pipe_culvert",
                    value=number(value),
                    cost_basis="CAD/m",
                    matrix_row="diameter_m=" + diameter,
                    table_reference="IAM Table 4-3 Part B",
                    source_page=83,
                    notes="Supply and delivery only; no installation.",
                )
                count += 1
            continue
        if len(cells) != 15 or not re.fullmatch(r"\d+", cells[0]):
            continue
        length = cells[0]
        for diameter, value in zip(diameters, cells[1:], strict=True):
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="culvert",
                component="culvert_installed",
                subcomponent="pipe_culvert",
                value=number(value),
                cost_basis="CAD/each",
                matrix_row=f"length_m={length}",
                matrix_column=f"diameter_m={diameter}",
                table_reference="IAM Table 4-3 Part A",
                source_page=83,
                notes="Installed culvert cost; no interpolation.",
            )
            count += 1
    if count != 12 * 14 + 14:
        raise ParseError(f"IAM Table 4-3 expected 182 cells, got {count}")
    return count


IAM_SUBGRADE_GROUPS = range(1, 13)
IAM_BRIDGE_SUPERSTRUCTURE = (
    ("supply_new_steel_structure_l100", "L100"),
    ("supply_new_steel_structure_l75_bcl625", "L75/BCL-625"),
    ("supply_new_deck_untreated_l100", "L100"),
    ("supply_new_deck_untreated_l75_bcl625", "L75/BCL-625"),
    ("supply_new_deck_treated_l100", "L100"),
    ("supply_new_deck_treated_l75_bcl625", "L75/BCL-625"),
    ("install_superstructure", ""),
    ("work_bridge_supply_install_remove", ""),
    ("dismantle_transport_reusable", ""),
)


def parse_iam_subgrade(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    section = text.split("4.3.2.3 Subgrade Construction Cost Equations", 1)[1]
    section = section.split("4.3.2.4", 1)[0]
    blocks: list[tuple[str, str]] = []
    current: tuple[str, str] | None = None
    for line in section.splitlines():
        match = re.match(r"^\s*(\d{1,2})\s+(.*)$", line)
        if match and 1 <= int(match.group(1)) <= 12:
            if current:
                blocks.append(current)
            current = (match.group(1), match.group(2).strip())
            continue
        if current and line.strip() and not line.strip().startswith("Where:"):
            current = (current[0], current[1] + " " + line.strip())
        elif line.strip().startswith("Where:"):
            break
    if current:
        blocks.append(current)
    count = 0
    for group, equation in blocks:
        if "Refer to section" in equation:
            continue
        base_match = re.match(r"^([\d,]+)\s*\+", equation)
        if not base_match:
            raise ParseError(f"IAM group {group} base missing: {equation}")
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="road_development",
            component="subgrade_construction_base",
            subcomponent="tabular_subgrade",
            value=number(base_match.group(1)),
            cost_basis="CAD/km",
            road_group=group,
            table_reference="IAM 4.3.2.3",
            source_page=81,
            unit_note="base before slope/LT/WET/BEC add-ons",
            notes="Road Group 1 uses the detailed ECE procedure, not this equation.",
        )
        slope = re.search(r"([\d,]+)\s*\*\s*SLOPE", equation)
        if slope:
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="road_development",
                component="subgrade_slope_addon",
                subcomponent="tabular_subgrade",
                value=number(slope.group(1)),
                cost_basis="CAD/km/%",
                road_group=group,
                table_reference="IAM 4.3.2.3",
                source_page=81,
                unit_note="per percentage point of uphill side slope",
            )
            count += 1
        lt = re.search(r"([\d,]+)\s*\*\s*LT", equation)
        if lt:
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="road_development",
                component="subgrade_long_term_addon",
                subcomponent="tabular_subgrade",
                value=number(lt.group(1)),
                cost_basis="CAD/km",
                road_group=group,
                table_reference="IAM 4.3.2.3",
                source_page=81,
            )
            count += 1
        wet = re.search(r"([\d,]+)\s*\*\s*WET", equation)
        if wet:
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="road_development",
                component="subgrade_wet_addon",
                subcomponent="tabular_subgrade",
                value=number(wet.group(1)),
                cost_basis="CAD/km",
                road_group=group,
                table_reference="IAM 4.3.2.3",
                source_page=81,
            )
            count += 1
        for value, bec in re.findall(
            r"([\d,]+)\s*\*\s*(ESSF|ICH_CWH|SBS|ICH|MS|CWH)", equation
        ):
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="road_development",
                component="subgrade_bec_addon",
                subcomponent=bec.lower(),
                value=number(value),
                cost_basis="CAD/km",
                road_group=group,
                table_reference="IAM 4.3.2.3",
                source_page=81,
            )
            count += 1
        count += 1
    if count < 30:
        raise ParseError(f"IAM subgrade rows unexpectedly low: {count}")
    return count


def parse_iam_stabilizing(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    section = text.split("4.3.4.1 Stabilizing Material Cost Equations", 1)[1]
    section = section.split("Where:", 1)[0]
    count = 0
    for line in section.splitlines():
        match = re.match(
            r"^\s*(\d{1,2})\s+([\d,]+)\s*\+\s*0\*D\s*\+\s*4,277\*RRQR",
            line,
        )
        if not match:
            continue
        group, base = match.group(1), number(match.group(2))
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="road_development",
            component="stabilizing_material_base",
            subcomponent="tabular_stabilizing",
            value=base,
            cost_basis="CAD/km",
            road_group=group,
            table_reference="IAM 4.3.4.1",
            source_page=87,
            notes="Road Group 1 uses ECE; snow/ice roads not eligible.",
        )
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="road_development",
            component="stabilizing_material_rrqr_addon",
            subcomponent="tabular_stabilizing",
            value=4277.0,
            cost_basis="CAD/km",
            road_group=group,
            table_reference="IAM 4.3.4.1",
            source_page=87,
            unit_note="add when ripped or quarry rock is used",
        )
        count += 2
    if count != 22:
        raise ParseError(f"IAM stabilizing expected 22 rows, got {count}")
    add_row(
        rows,
        source=source,
        region="Interior",
        component_group="road_development",
        component="snow_ice_road",
        subcomponent="new_construction",
        value=9814.0,
        cost_basis="CAD/km",
        table_reference="IAM 4.3.2.5",
        source_page=82,
        notes="Single-lane seasonal snow/ice road; no stabilizing material allowed.",
    )
    return count + 1


def parse_iam_bridge(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 4-4:", 1)
    if len(parts) != 2:
        raise ParseError("IAM Table 4-4 not found")
    section = parts[1].split("4.3.3.3", 1)[0]
    lines = [clean(line) for line in section.splitlines() if clean(line)]
    four_value_rows: list[list[float]] = []
    for line in lines:
        scan_line = re.sub(r"L\d+(?:/BCL-\d+)?|BCL-\d+", " ", line)
        tokens = re.findall(r"(?<![\w.])(\d[\d,]*)(?![\w])", scan_line)
        if len(tokens) == 4:
            values = [number(token) for token in tokens]
            if all(value > 100 for value in values):
                four_value_rows.append(values)
    if len(four_value_rows) < 9:
        raise ParseError(f"IAM bridge superstructure rows: {four_value_rows}")
    count = 0
    for (component, design), values in zip(
        IAM_BRIDGE_SUPERSTRUCTURE, four_value_rows[:9], strict=True
    ):
        for span, value in zip(("6", "9", "12", "15"), values, strict=True):
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="bridge",
                component=component,
                subcomponent="superstructure",
                value=value,
                cost_basis="CAD/each",
                matrix_row=f"span_m={span}",
                matrix_column=f"design={design}" if design else "",
                table_reference="IAM Table 4-4",
                source_page=84,
                notes="Tabular bridge component; no interpolation.",
            )
            count += 1

    substructure_labels = (
        ("sill_log", r"Sill \(Log\)\s+([\d,]+)\s+([\d,]+)", 2),
        ("lock_block_supply_new", r"Supply \(new only\)\s+([\d,]+)\s+([\d,]+)", 2),
        ("lock_block_install", r"\bInstall\s+([\d,]+)\s+([\d,]+)", 2),
        (
            "dismantle_transport_used_blocks",
            r"Dismantle and Transport of Used Concrete Blocks\s+([\d,]+)\s+([\d,]+)",
            2,
        ),
        ("cap_12x12_treated", r"12x12 Treated Timber\s+([\d,]+)", 1),
        ("cap_16x16_treated", r"16x16 Treated Timber\s+([\d,]+)", 1),
        ("bearings_precast_concrete", r"Pre-cast Concrete(?! Pad)\s+([\d,]+)", 1),
        ("bearings_untreated_timber", r"Untreated Timber\s+([\d,]+)", 1),
        (
            "bearings_treated_timber",
            r"(?<!12x12 )(?<!16x16 )Treated Timber\s+([\d,]+)",
            1,
        ),
        ("sill_precast_concrete_pad", r"Pre-cast Concrete Pad\s+([\d,]+)", 1),
        (
            "sill_cast_in_place_concrete_pad",
            r"Cast-in-Place Concrete Pad\s+([\d,]+)",
            1,
        ),
        ("survey_site_specific", r"Site Specific\s+([\d,]+)", 1),
        ("survey_standard", r"\bStandard\s+([\d,]+)", 1),
        ("certification", r"Certification.*?([\d,]+)\s+([\d,]+)", 2),
    )
    for component, pattern, expected in substructure_labels:
        match = re.search(pattern, "\n".join(lines), re.DOTALL)
        if match is None:
            raise ParseError(f"IAM bridge row missing: {component}")
        values = [number(group) for group in match.groups()]
        if len(values) != expected:
            raise ParseError(f"IAM bridge {component} values: {values}")
        if expected == 2:
            for column, value in zip(
                ("abutment_le_0_9m_or_1_tier", "abutment_gt_0_9_le_1_8m_or_2_tiers"),
                values,
                strict=True,
            ):
                add_row(
                    rows,
                    source=source,
                    region="Interior",
                    component_group="bridge",
                    component=component,
                    subcomponent="substructure",
                    value=value,
                    cost_basis="CAD/each",
                    matrix_column=column,
                    table_reference="IAM Table 4-4",
                    source_page=84,
                )
                count += 1
        else:
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="bridge",
                component=component,
                subcomponent="substructure",
                value=values[0],
                cost_basis="CAD/each",
                table_reference="IAM Table 4-4",
                source_page=84,
            )
            count += 1
    return count


def parse_iam_road_maintenance(
    tables: Sequence[Sequence[Sequence[str | None]]],
    source: Source,
    rows: list[dict[str, object]],
) -> int:
    table = next(
        (item for item in tables if any("RMG" in flatten(row) for row in item)),
        None,
    )
    if table is None:
        raise ParseError("IAM Table 4-6 not found")
    values: dict[str, float] = {}
    descriptions: dict[str, list[str]] = {}
    current = ""
    for raw in table:
        cells = flatten(raw)
        if not cells:
            continue
        if len(cells[0]) == 1 and cells[0].isalpha():
            current = cells[0]
            cells = cells[1:]
        if not current:
            continue
        last = cells[-1] if cells else ""
        numeric = (
            number(last)
            if re.fullmatch(r"[\d,]+(?:\.\d+)?", last)
            else None
        )
        if numeric is not None:
            values[current] = numeric
            descriptions.setdefault(current, []).extend(cells[:-1])
        else:
            descriptions.setdefault(current, []).extend(cells)
    if len(values) != 22:
        raise ParseError(f"IAM Table 4-6 expected 22 RMGs, got {values}")
    for rmg, value in values.items():
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="road_management",
            component="road_maintenance",
            subcomponent="road_maintenance_group",
            zone_basis="road_maintenance_group",
            zone=rmg,
            value=value,
            cost_basis="CAD/m3",
            table_reference="IAM Table 4-6",
            source_page=95,
            notes="; ".join(descriptions.get(rmg, []))[:500],
        )
    return len(values)


def parse_iam_rail(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 3-5: Railway Transportation", 1)
    if len(parts) != 2:
        raise ParseError("IAM Table 3-5 not found")
    section = parts[1]
    section = section.split("3.3.2.2", 1)[0]
    count = 0
    for line in section.splitlines():
        match = re.match(
            r"^\s*(Leo Creek|Lovell|Bear Lake|Minaret Creek|Niteal)\s+"
            r"\$(\d+(?:\.\d+)?)/m3\s+(.+?)\s*$",
            line,
        )
        if not match:
            continue
        origin, value, poa = match.groups()
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="hauling",
            component="railway_transportation",
            subcomponent="point_of_origin",
            zone_basis="point_of_appraisal",
            zone=clean(poa),
            value=number(value),
            cost_basis="CAD/m3",
            matrix_row=f"origin={origin}",
            table_reference="IAM Table 3-5",
            source_page=62,
            notes="Rail specified operation; combined with truck-to-rail transfer where used.",
        )
        count += 1
    if count != 5:
        raise ParseError(f"IAM railway rows expected 5, got {count}")
    return count


def parse_iam_specified(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    def value(pattern: str, *, last: bool = True) -> float:
        matches = re.findall(pattern, text, re.DOTALL)
        if not matches:
            raise ParseError(f"IAM specified operation not found: {pattern}")
        return number(matches[-1] if last else matches[0])

    values = (
        (
            "dump_boom_reservoir_marine",
            "",
            10.13,
            r"Reservoir and Marine\s*=\s*\$(\d+(?:\.\d+)?)/m3",
        ),
        ("dump_boom_natural_lake", "", 4.71, r"Natural Lake\s*=\s*\$(\d+(?:\.\d+)?)/m3"),
        ("dewater_reload", "", 6.48, r"Dewater and Reload\s*=\s*\$(\d+(?:\.\d+)?)/m3"),
        ("log_barge", "", 23.08, r"Log Barge\s*=\s*\$(\d+(?:\.\d+)?)/m3"),
        ("truck_to_rail_transfer", "", 4.37, r"truck-to-rail transfer\s*=\s*\$(\d+(?:\.\d+)?)/m3"),
        (
            "barge_used_for_truck_haul",
            "",
            9.43,
            r"Barge transportation \(used for truck haul\).*?\$(\d+(?:\.\d+)?)/m3",
        ),
        (
            "barge_not_used_for_truck_haul",
            "",
            1.66,
            r"Barge Transportation \(Not Used for Truck Haul\).*?The cost estimate \(regardless of ownership\) is \$(\d+(?:\.\d+)?)/m3",
        ),
        (
            "skyline",
            "skyline",
            3.25,
            r"specified operation cost estimate is:? \$(\d+(?:\.\d+)?)/m3 for the harvest method volume appraised\s+as\s+skyline",
        ),
        (
            "helicopter",
            "",
            94.31,
            r"The specified operation cost estimate is \$(\d+(?:\.\d+)?)/m3 for the harvest method volume appraised\s+as\s+Heli",
        ),
        (
            "horse",
            "",
            8.67,
            r"The specified operation cost estimate is \$(\d+(?:\.\d+)?)/m3 for the harvest method volume appraised\s+as\s+horse",
        ),
    )
    for component, subcomponent, expected, pattern in values:
        found = value(pattern, last=component != "dump_boom_natural_lake")
        if abs(found - expected) > 0.001:
            raise ParseError(f"IAM {component}: expected {expected}, got {found}")
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="specified_operations",
            component=component,
            subcomponent=subcomponent,
            value=found,
            cost_basis="CAD/m3",
            table_reference="IAM 3.3",
            source_page={
                "dump_boom_reservoir_marine": 61,
                "dump_boom_natural_lake": 61,
                "dewater_reload": 61,
                "log_barge": 62,
                "truck_to_rail_transfer": 62,
                "barge_used_for_truck_haul": 62,
                "barge_not_used_for_truck_haul": 62,
                "skyline": 63,
                "helicopter": 63,
                "horse": 63,
            }[component],
            notes="Specified operation allowance, subject to eligibility and CPI adjustment.",
        )
    add_row(
        rows,
        source=source,
        region="Interior",
        component_group="specified_operations",
        component="surface_tow_rate_coefficient",
        subcomponent="reservoir_marine",
        value=0.0389,
        min_value=1.69,
        cost_basis="CAD/m3/km",
        table_reference="IAM 3.3.1.1",
        source_page=61,
        unit_note="rate = 0.0389 * one-way km - 0.8744; floor $1.69/m3",
        notes="Intercept -0.8744 is applied in the formula; floor is captured as min_value.",
    )
    add_row(
        rows,
        source=source,
        region="Interior",
        component_group="tenure_obligation",
        component="high_development_cost_threshold",
        subcomponent="BCTS_only",
        value=5.93,
        cost_basis="CAD/m3",
        table_reference="IAM 3.3.6",
        source_page=64,
        notes="HDC = DC - 3.27; HDC = 0 when DC <= 5.93.",
    )
    return len(values) + 2


def parse_iam_bec(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 4-8:", 1)
    if len(parts) != 2:
        raise ParseError("IAM Table 4-8 not found")
    section = parts[1].split("Expired and Invalid Variants", 1)[0]
    mode = ""
    seen: set[tuple[str, str]] = set()
    count = 0
    for line in section.splitlines():
        stripped = clean(line)
        if stripped == "Basic Silviculture":
            mode = "basic"
            continue
        if stripped == "Enhanced Silviculture":
            mode = "enhanced"
            continue
        if stripped == "Wildfire Enhanced Silviculture":
            mode = "wildfire_enhanced"
            continue
        if mode == "":
            continue
        for unit, raw_value in re.findall(
            r"([A-Z]{2,5}[a-z0-9]*\*?)\s+([\d,]+)", line
        ):
            unit = unit.rstrip("*")
            key = (mode, unit)
            if key in seen:
                raise ParseError(f"duplicate BEC row: {key}")
            seen.add(key)
            add_row(
                rows,
                source=source,
                region="Interior",
                component_group="silviculture",
                component={
                    "basic": "basic_silviculture",
                    "enhanced": "enhanced_silviculture",
                    "wildfire_enhanced": "wildfire_enhanced_silviculture",
                }[mode],
                subcomponent="bec_unit",
                zone_basis="bec_unit",
                zone=unit,
                value=number(raw_value),
                cost_basis="CAD/ha",
                table_reference="IAM Table 4-8",
                source_page=104,
                notes="Net of overhead; fallback to undifferentiated 'un' subzone when unit not listed.",
            )
            count += 1
    if count < 200:
        raise ParseError(f"IAM Table 4-8 rows unexpectedly low: {count}")
    if sum(1 for mode, _ in seen if mode == "basic") < 150:
        raise ParseError("IAM Table 4-8 basic rows unexpectedly low")
    return count


def parse_iam_toa_parameters(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    count = 0
    add_row(
        rows,
        source=source,
        region="Interior",
        component_group="tenure_obligation",
        component="root_disease_control_base",
        subcomponent="treatment_area",
        value=1114.0,
        min_value=1200.0,
        max_value=1469.0,
        cost_basis="CAD/ha",
        table_reference="IAM 4.6",
        source_page=102,
        unit_note="base + 6.10*slope + 0.44*stems/ha; clamped to min/max",
        notes="Treatment area cost per hectare before slope and stem adders.",
    )
    count += 1
    for component, pattern, expected in (
        ("root_disease_control_slope_addon", r"6\.10 \* SLOPE", 6.10),
        ("root_disease_control_sph_addon", r"0\.44 \* SPH", 0.44),
    ):
        found = re.search(pattern, text)
        if not found:
            raise ParseError(f"IAM TOA parameter missing: {component}")
        add_row(
            rows,
            source=source,
            region="Interior",
            component_group="tenure_obligation",
            component=component,
            subcomponent="treatment_area",
            value=expected,
            cost_basis="CAD/ha/%" if "slope" in component else "CAD/ha/stem",
            table_reference="IAM 4.6",
            source_page=102,
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# CAM tables


def parse_cam_road(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 5-1:", 1)
    if len(parts) != 2:
        raise ParseError("Table 5-1: not found")
    section = parts[1].split("5.3.3.2", 1)[0]
    aliases = {
        "OMLB": ("overland_movable_log_bridge", "n/a"),
        "OMPR": ("overland_movable_permanent", "n/a"),
        "OMRB": ("overland_movable_rock_bolster", "n/a"),
        "TOE": ("toe_of_slope", "up to 1.50"),
        "MRK": ("medium_rock", "1.51-3.00"),
        "HRK": ("high_rock", "3.01-4.50"),
        "XRK": ("extra_rock", "4.51-6.00"),
        "XXRK": ("extra_extra_rock", "6.01-7.50"),
    }
    count = 0
    for line in section.splitlines():
        cells = clean(line).split()
        if not cells:
            continue
        code = cells[0]
        if code in aliases:
            name, height = aliases[code]
            values = [number(token) for token in cells if re.fullmatch(r"[\d,]+", token)]
            if len(values) != 2:
                raise ParseError(f"CAM road row {code}: {cells}")
            for hardness, value in zip(("soft_medium", "hard"), values, strict=True):
                add_row(
                    rows,
                    source=source,
                    region="Coast",
                    component_group="road_development",
                    component="road_construction",
                    subcomponent=name,
                    value=value,
                    cost_basis="CAD/km",
                    matrix_row=f"bank_height_category={code};rock_face_height_m={height}",
                    matrix_column=f"rock={hardness}",
                    table_reference="CAM Table 5-1",
                    source_page=74,
                    notes="Tabular road construction cost; no adjustment except published add-ons.",
                )
                count += 1
        elif code == "Isolated":
            values = [number(token) for token in cells if token.startswith("+")]
            for hardness, value in zip(("soft_medium", "hard"), values, strict=True):
                add_row(
                    rows,
                    source=source,
                    region="Coast",
                    component_group="road_development",
                    component="road_construction_addon_isolated",
                    subcomponent="isolated",
                    value=value,
                    cost_basis="CAD/km",
                    matrix_column=f"rock={hardness}",
                    table_reference="CAM Table 5-1",
                    source_page=74,
                )
                count += 1
        elif "DRSE" in line:
            values = [number(token) for token in cells if token.startswith("+")]
            for hardness, value in zip(("soft_medium", "hard"), values, strict=True):
                add_row(
                    rows,
                    source=source,
                    region="Coast",
                    component_group="road_development",
                    component="road_construction_addon_remote_poa",
                    subcomponent="DRSE_GKIN_GRIS_MIDC_NTHC",
                    value=value,
                    cost_basis="CAD/km",
                    matrix_column=f"rock={hardness}",
                    table_reference="CAM Table 5-1",
                    source_page=74,
                )
                count += 1
    if count != 20:
        raise ParseError(f"CAM Table 5-1 expected 20 rows, got {count}")
    return count


def parse_cam_log_bridge(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 5-2:", 1)
    if len(parts) != 2:
        raise ParseError("Table 5-2: not found")
    section = parts[1].split("5.3.3.2.2", 1)[0]
    pending_span: str | None = None
    count = 0
    for line in section.splitlines():
        tokens = clean(line).split()
        if not tokens:
            continue
        if len(tokens) == 1 and tokens[0].isdigit():
            pending_span = tokens[0]
            continue
        numbers_found = [token for token in tokens if re.fullmatch(r"[\d.]+", token)]
        if len(numbers_found) == 6:
            span = numbers_found[0]
            values = numbers_found[1:]
        elif len(numbers_found) == 5 and pending_span:
            span = pending_span
            values = numbers_found
        else:
            continue
        pending_span = None
        columns = (
            "single_log_sill",
            "multi_log_crib_published_1",
            "multi_log_crib_published_2",
            "multi_log_crib_published_3",
            "multi_log_crib_published_4",
        )
        for column, raw_value in zip(columns, values, strict=True):
            add_row(
                rows,
                source=source,
                region="Coast",
                component_group="bridge",
                component="log_bridge",
                subcomponent="single_log_sill" if column == "single_log_sill" else "multi_log_crib",
                value=number(raw_value) * 1000,
                cost_basis="CAD/each",
                matrix_row=f"span_m={span}",
                matrix_column=column,
                table_reference="CAM Table 5-2",
                source_page=76,
                unit_note="published in thousands of dollars; normalized to CAD",
                notes=(
                    "Table 5-2 has five published data columns after span: "
                    "a Single Log Sill column plus four Multi-Log Crib columns. "
                    "Column labels are preserved in printed order; no interpolation."
                ),
            )
            count += 1
    if count != 85:
        raise ParseError(f"CAM Table 5-2 expected 85 cells, got {count}")
    return count


def parse_cam_permanent_bridge(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 5-3:", 1)
    if len(parts) != 2:
        raise ParseError("Table 5-3: not found")
    section = parts[1].split("5.3.3.2.3", 1)[0]
    count = 0
    for line in section.splitlines():
        tokens = clean(line).split()
        values = [token for token in tokens if re.fullmatch(r"[\d.]+", token)]
        if len(values) != 6:
            continue
        span, heights = values[0], values[1:]
        for height, raw_value in zip(("0", "1", "2", "3", "4"), heights, strict=True):
            add_row(
                rows,
                source=source,
                region="Coast",
                component_group="bridge",
                component="permanent_portable_bridge",
                subcomponent="steel_or_other",
                value=number(raw_value) * 1000,
                cost_basis="CAD/each",
                matrix_row=f"span_m={span}",
                matrix_column=f"abutment_height_m={height}",
                table_reference="CAM Table 5-3",
                source_page=78,
                unit_note="published in thousands of dollars; normalized to CAD",
            )
            count += 1
    if count != 120:
        raise ParseError(f"CAM Table 5-3 expected 120 cells, got {count}")
    return count


def parse_cam_culverts(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    parts = text.rsplit("Table 5-4: Culvert Costs", 1)
    if len(parts) != 2:
        raise ParseError("Table 5-4: Culvert Costs not found")
    section = parts[1].split("5.3.4", 1)[0]
    count = 0
    for line in section.splitlines():
        matches = re.findall(
            r"(0\.\d|1\.\d)\s+\$([\d.]+)\s+\$([\d.]+)", line
        )
        for diameter, installed, material in matches:
            add_row(
                rows,
                source=source,
                region="Coast",
                component_group="culvert",
                component="pipe_culvert_installed",
                subcomponent="pipe_culvert",
                value=number(installed),
                cost_basis="CAD/m",
                matrix_row=f"diameter_m={diameter}",
                table_reference="CAM Table 5-4 Column A",
                source_page=79,
            )
            add_row(
                rows,
                source=source,
                region="Coast",
                component_group="culvert",
                component="pipe_culvert_materials",
                subcomponent="pipe_culvert",
                value=number(material),
                cost_basis="CAD/m",
                matrix_row=f"diameter_m={diameter}",
                table_reference="CAM Table 5-4 Column B",
                source_page=79,
            )
            count += 2
    if count != 24:
        raise ParseError(f"CAM Table 5-4 expected 24 cells, got {count}")
    add_row(
        rows,
        source=source,
        region="Coast",
        component_group="culvert",
        component="wood_culvert",
        subcomponent="wood_culvert",
        value=2700.0,
        cost_basis="CAD/each",
        table_reference="CAM 5.3.3.2.3(2)",
        source_page=79,
        notes="All wood culverts up to 3.4 m span.",
    )
    return count + 1


def parse_cam_silviculture(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    section = text.split("Table 5-6: BEC Silviculture Cost Estimates", 1)[1]
    section = section.split("5.7.1", 1)[0]
    count = 0
    for raw_value, unit in re.findall(
        r"(\d[\d,]*)\s+(CDF|CWH[a-z0-9]*|ESSF|MH|Other)\b", section
    ):
        add_row(
            rows,
            source=source,
            region="Coast",
            component_group="silviculture",
            component="basic_silviculture",
            subcomponent="bec_unit",
            zone_basis="bec_unit",
            zone=unit,
            value=number(raw_value),
            cost_basis="CAD/ha",
            table_reference="CAM Table 5-6",
            source_page=87,
            notes="Fallback priority: variant, then subzone, then zone; 'Other' for IDF.",
        )
        count += 1
    if count != 15:
        raise ParseError(f"CAM Table 5-6 expected 15 rows, got {count}")
    return count


def parse_cam_toa_parameters(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    count = 0
    specifications = (
        (
            "forest_planning_administration",
            "scale_based",
            21.56,
            "CAD/m3",
            r"The total forest planning and administration cost is \$(\d+(?:\.\d+)?)/m3 for scale based",
        ),
        (
            "low_volume_cost",
            "eligible_small_volume",
            10.89,
            "CAD/m3",
            r"A low volume cost of \$(\d+(?:\.\d+)?)/m3 may be included",
        ),
        (
            "road_management",
            "scale_based",
            4.73,
            "CAD/m3",
            r"The road management cost is \$(\d+(?:\.\d+)?)/m3 for scale based",
        ),
        (
            "root_disease_control_base",
            "treatment_area",
            1137.19,
            "CAD/ha",
            r"Treatment Area Cost per Hectare = ([\d,]+\.\d+)",
        ),
    )
    for component, subcomponent, expected, basis, pattern in specifications:
        match = re.search(pattern, text)
        if not match:
            raise ParseError(f"CAM TOA parameter missing: {component}")
        found = number(match.group(1))
        if abs(found - expected) > 0.001:
            raise ParseError(f"CAM {component}: expected {expected}, got {found}")
        add_row(
            rows,
            source=source,
            region="Coast",
            component_group="tenure_obligation",
            component=component,
            subcomponent=subcomponent,
            value=found,
            min_value=1224.0 if component == "root_disease_control_base" else None,
            max_value=1560.0 if component == "root_disease_control_base" else None,
            cost_basis=basis,
            table_reference="CAM 5.2/5.2.1/5.4/5.7.1",
            source_page={
                "forest_planning_administration": 62,
                "low_volume_cost": 62,
                "road_management": 82,
                "root_disease_control_base": 88,
            }[component],
            notes="Scale-based amount; cruise-based FPA and road management use CTC.",
        )
        count += 1
    for component, expected, pattern, basis in (
        (
            "root_disease_control_slope_addon",
            5.35,
            r"5\.35\* SLOPE",
            "CAD/ha/%",
        ),
        (
            "root_disease_control_sph_addon",
            0.33,
            r"0\.33\* SPH",
            "CAD/ha/stem",
        ),
    ):
        if not re.search(pattern, text):
            raise ParseError(f"CAM TOA parameter missing: {component}")
        add_row(
            rows,
            source=source,
            region="Coast",
            component_group="tenure_obligation",
            component=component,
            subcomponent="treatment_area",
            value=expected,
            cost_basis=basis,
            table_reference="CAM 5.7.1",
            source_page=88,
        )
        count += 1
    return count


def parse_cam_specified(
    text: str, source: Source, rows: list[dict[str, object]]
) -> int:
    specifications = (
        (
            "inland_water_transportation",
            "entire_cutting_authority",
            12.10,
            "CAD/m3",
            r"entirely inland water transportation, the adjustment shall be\s+\$(\d+(?:\.\d+)?)/m3",
            "4.4.1",
            54,
        ),
        (
            "clayoquot_sound_operating",
            "entire_area",
            13.02,
            "CAD/m3",
            r"located entirely within the Clayoquot Sound area, the adjustment shall be\s+\$(\d+(?:\.\d+)?)/m3",
            "4.4.2",
            55,
        ),
        (
            "tree_crown_modification",
            "old_growth",
            28.42,
            "CAD/tree",
            r"each old growth coniferous tree that is modified is \$(\d+(?:\.\d+)?)",
            "4.4.3",
            55,
        ),
        (
            "tree_crown_modification",
            "second_growth",
            15.46,
            "CAD/tree",
            r"each second growth coniferous tree that is modified is \$(\d+(?:\.\d+)?)",
            "4.4.3",
            55,
        ),
        (
            "ecosystem_based_management",
            "wholly_within_area",
            9.39,
            "CAD/m3",
            r"located wholly within that part[\s\S]*?the adjustment shall be\s+\$(\d+(?:\.\d+)?)/m3",
            "4.4.4",
            55,
        ),
        (
            "barging_transportation",
            "Graham_Island",
            11.74,
            "CAD/m3",
            r"GRIS\s+(\d+(?:\.\d+)?)",
            "4.4.7",
            57,
        ),
        (
            "barging_transportation",
            "non_Graham_Island",
            9.68,
            "CAD/m3",
            r"Non-GRIS\s+(\d+(?:\.\d+)?)",
            "4.4.7",
            57,
        ),
    )
    count = 0
    for component, subcomponent, expected, basis, pattern, section, page in specifications:
        match = re.search(pattern, text)
        if not match:
            raise ParseError(f"CAM specified operation missing: {component}")
        found = number(match.group(1))
        if abs(found - expected) > 0.001:
            raise ParseError(f"CAM {component}: expected {expected}, got {found}")
        add_row(
            rows,
            source=source,
            region="Coast",
            component_group="specified_operations",
            component=component,
            subcomponent=subcomponent,
            operability=subcomponent if "growth" in subcomponent else "",
            value=found,
            cost_basis=basis,
            table_reference=f"CAM {section}",
            source_page=page,
            notes="Specified operation allowance, subject to eligibility and CPI adjustment.",
        )
        count += 1
    add_row(
        rows,
        source=source,
        region="Coast",
        component_group="specified_operations",
        component="long_haul_rate",
        subcomponent="distance_beyond_100_km",
        value=0.171,
        cost_basis="CAD/m3/km",
        table_reference="CAM 4.4.5",
        source_page=56,
        unit_note="LHC = (HD - 100) * 0.171; zero when HD <= 100 km",
        notes="Long-haul specified operation rate.",
    )
    add_row(
        rows,
        source=source,
        region="Coast",
        component_group="tenure_obligation",
        component="high_development_cost_threshold",
        subcomponent="BCTS_only",
        value=13.76,
        cost_basis="CAD/m3",
        table_reference="CAM 4.4.6",
        source_page=56,
        notes="HDC = DC - 8.23; HDC = 0 when DC <= 13.76.",
    )
    return count + 2


# ---------------------------------------------------------------------------
# Assembly / validation


def build_sources(pdf_dir: Path) -> dict[str, Source]:
    metadata = (
        ("iam", "IAM 2026", "iam_2026_master_b.pdf", IAM_URL, "2026-07-01", 172),
        ("cam", "CAM 2026", "cam_2026_master.pdf", CAM_URL, "2026-01-01", 145),
        (
            "silv_oct",
            "Interior Basic Silviculture 2026-10",
            "basic_silv_costs_oct_26.pdf",
            SILV_URL,
            "2026-10-01",
            1,
        ),
        (
            "silv_jul",
            "Interior Basic Silviculture 2026-07",
            "basic_silv_costs_jul_26.pdf",
            SILV_JUL_URL,
            "2026-07-01",
            1,
        ),
    )
    sources: dict[str, Source] = {}
    for key, label, filename, url, effective, pages in metadata:
        path = pdf_dir / filename
        if not path.exists():
            raise SystemExit(f"missing source PDF: {path}")
        actual_pages = page_count(path)
        if actual_pages != pages:
            raise SystemExit(f"{filename}: expected {pages} pages, got {actual_pages}")
        retrieved = datetime.fromtimestamp(
            path.stat().st_mtime, UTC
        ).replace(microsecond=0).isoformat()
        sources[key] = Source(
            key=key,
            label=label,
            filename=filename,
            url=url,
            effective_date=effective,
            retrieved_utc=retrieved,
            sha256=sha256_file(path),
            pages=actual_pages,
        )
    return sources


def coverage_rows() -> list[dict[str, object]]:
    rows = [
        ("IAM", "Table 1-1/Table 3-5", "specified_operations", "parsed", "Appraisal zones, POAs, railway origins"),
        ("IAM", "3.3 Specified operations", "specified_operations", "parsed", "Water, rail, barge, skyline, helicopter, horse"),
        ("IAM", "3.3.1.1 Surface tow", "specified_operations", "parsed_formula", "Coefficient and floor; intercept is in notes"),
        ("IAM", "3.3.6 High development cost", "specified_operations", "parsed_rule", "BCTS-only formula and threshold"),
        ("IAM", "Table 4-1 FMA constants", "tenure_obligation", "parsed", "6 appraisal regions with min/max"),
        ("IAM", "4.3.2.3 Subgrade equations", "road_development", "parsed", "12 road groups; group 1 is ECE"),
        ("IAM", "4.3.2.5 Snow/ice roads", "road_development", "parsed", "Single $/km value"),
        ("IAM", "Table 4-3 Culverts", "culvert", "parsed", "Part A 168 installed cells; Part B 14 supply cells"),
        ("IAM", "Table 4-4 Bridges", "bridge", "parsed", "Superstructure and substructure components"),
        ("IAM", "4.3.4.1 Stabilizing material", "road_development", "parsed", "11 equation groups plus RRQR add-on"),
        ("IAM", "Table 4-6 Road maintenance", "road_management", "parsed", "22 RMGs"),
        ("IAM", "Table 4-8 BEC silviculture", "silviculture", "parsed", "Basic, enhanced, wildfire enhanced; expired rows excluded"),
        ("IAM", "Table 4-7/4-9 adjustment factors", "silviculture", "parsed_policy_parameter", "Factors recorded in manifest, not cost rows"),
        ("IAM", "Tree-to-truck / logging", "tree_to_truck", "not_published", "No public $/m3 allowance table; ILCR microdata not used"),
        ("IAM", "Hauling", "hauling", "partial", "Rail/water/barge SO captured; no general truck-haul $/m3 rate"),
        ("IAM", "Appendix I equipment/labour", "road_development", "partial", "ECE rates are input rates, not a unit allowance table"),
        ("CAM", "4.4 Specified operations", "specified_operations", "parsed", "Water, Clayoquot, tree crown, EBM, long haul, barging, HDC"),
        ("CAM", "Table 5-1 Road costs", "road_development", "parsed", "8 bank categories x 2 rock classes + 2 add-ons"),
        ("CAM", "Table 5-2 Log bridges", "bridge", "parsed", "17 spans x 5 published cost columns; single log sill + four multi-log crib columns"),
        ("CAM", "Table 5-3 Permanent/portable bridges", "bridge", "parsed", "24 spans x 5 abutment heights"),
        ("CAM", "Table 5-4 Culverts", "culvert", "parsed", "12 diameters x installed/materials; wood culvert flat rate"),
        ("CAM", "Table 5-5 ECE trend factors", "road_development", "parsed_policy_parameter", "Trend factors only; ECE projects are site-specific"),
        ("CAM", "Table 5-6 BEC silviculture", "silviculture", "parsed", "15 BEC classes"),
        ("CAM", "5.2/5.4/5.7.1/5.11 TOA", "tenure_obligation", "parsed", "FPA, low volume, road management, root disease caps"),
        ("CAM", "Logging", "logging", "not_published", "No public $/m3 logging allowance table; EWB embeds logging"),
        ("CAM", "Appendix I equipment/labour", "road_development", "partial", "ECE input rates, not a unit allowance table"),
        ("Interior Basic Silviculture", "2026-10-01", "silviculture", "parsed", "7 forest zones x all-species average"),
        ("Interior Basic Silviculture", "2026-07-01", "silviculture", "parsed", "7 forest zones x all-species average"),
    ]
    return [
        {
            "region": row[0],
            "table_or_section": row[1],
            "component_group": row[2],
            "status": row[3],
            "notes": row[4],
        }
        for row in rows
    ]


FIELDS = [
    "record_id",
    "jurisdiction",
    "region",
    "source_series",
    "component_group",
    "component",
    "subcomponent",
    "species",
    "operability",
    "zone_basis",
    "zone",
    "road_group",
    "effective_date",
    "year",
    "cost_basis",
    "value",
    "min_value",
    "max_value",
    "matrix_row",
    "matrix_column",
    "unit_note",
    "table_reference",
    "source_page",
    "source_document",
    "source_url",
    "source_sha256",
    "observation_type",
    "notes",
]
COVERAGE_FIELDS = [
    "region",
    "table_or_section",
    "component_group",
    "status",
    "notes",
]
ALLOWED_BASIS = {
    "CAD/m3",
    "CAD/km",
    "CAD/ha",
    "CAD/m",
    "CAD/each",
    "CAD/tree",
    "CAD/m3/km",
    "CAD/km/%",
    "CAD/ha/%",
    "CAD/ha/stem",
}


def validate(
    rows: Sequence[dict[str, object]],
    coverage: Sequence[dict[str, object]],
) -> dict[str, object]:
    if not rows:
        raise SystemExit("no cost rows extracted")
    ids = [str(row["record_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate record_id")
    keys = {
        (
            row["source_document"],
            row["component"],
            row["subcomponent"],
            row["operability"],
            row["zone"],
            row["road_group"],
            row["matrix_row"],
            row["matrix_column"],
        )
        for row in rows
    }
    if len(keys) != len(rows):
        from collections import Counter

        counts = Counter(
            (
                row["source_document"],
                row["component"],
                row["zone"],
                row["road_group"],
                row["matrix_row"],
                row["matrix_column"],
            )
            for row in rows
        )
        duplicates = [key for key, count in counts.items() if count > 1]
        raise SystemExit(f"duplicate observation keys: {duplicates[:5]}")
    for row in rows:
        value = row["value"]
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise SystemExit(f"non-finite value: {row}")
        if float(value) < 0:
            raise SystemExit(f"negative value: {row}")
        if row["cost_basis"] not in ALLOWED_BASIS:
            raise SystemExit(f"unexpected cost_basis: {row['cost_basis']}")
        for bound in ("min_value", "max_value"):
            if row[bound] is not None and float(row[bound]) < 0:
                raise SystemExit(f"negative bound: {row}")
        if (
            row["min_value"] is not None
            and row["max_value"] is not None
            and float(row["min_value"]) > float(row["max_value"])
        ):
            raise SystemExit(f"min > max: {row}")
    if not any(row["extraction_status"] == "parsed" for row in coverage):
        raise SystemExit("coverage has no parsed tables")
    return {
        "rows": len(rows),
        "components": len({row["component"] for row in rows}),
        "records_by_region": {
            region: sum(1 for row in rows if row["region"] == region)
            for region in sorted({str(row["region"]) for row in rows})
        },
        "records_by_basis": {
            basis: sum(1 for row in rows if row["cost_basis"] == basis)
            for basis in sorted({str(row["cost_basis"]) for row in rows})
        },
    }


def spot_checks(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    checks = [
        ("IAM 2026", "forest_management_administration_constant", "Skeena", "value", 16.71),
        ("IAM 2026", "basic_silviculture", "ICHmw1", "value", 2973.0),
        ("IAM 2026", "culvert_installed", "length_m=9|diameter_m=0.3", "value", 963.0),
        ("IAM 2026", "subgrade_construction_base", "12", "value", 18975.0),
        ("CAM 2026", "road_construction", "OMLB", "value", 72601.0),
        ("CAM 2026", "log_bridge", "span_m=4|single_log_sill", "value", 1300.0),
        ("CAM 2026", "permanent_portable_bridge", "span_m=2|abutment_height_m=0", "value", 52700.0),
        ("CAM 2026", "pipe_culvert_installed", "diameter_m=1.8", "value", 996.0),
        ("CAM 2026", "basic_silviculture", "CWH", "value", 2178.0),
        ("Interior Basic Silviculture 2026-10", "basic_silviculture", "North Central Zone", "value", 10.02),
        ("Interior Basic Silviculture 2026-07", "basic_silviculture", "North Central Zone", "value", 9.95),
    ]
    results: list[dict[str, object]] = []
    for series, component, key, field, expected in checks:
        candidates = [
            row
            for row in rows
            if row["source_series"] == series
            and row["component"] == component
            and all(
                any(
                    part == str(row[field]) or part in str(row[field])
                    for field in ("zone", "road_group", "matrix_row", "matrix_column")
                )
                for part in key.split("|")
            )
        ]
        if not candidates:
            raise SystemExit(f"spot check missing: {series} {component} {key}")
        found = candidates[0][field]
        if abs(float(found) - expected) > 0.001:
            raise SystemExit(
                f"spot check failed: {series} {component} {key}: {found} != {expected}"
            )
        results.append(
            {
                "source_series": series,
                "component": component,
                "selector": key,
                "expected": expected,
                "found": found,
                "status": "pass",
            }
        )
    return results


def write_csv(path: Path, rows: Iterable[dict[str, object]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    sources = build_sources(args.pdf_dir)
    texts = {
        key: "\n".join(read_pages(args.pdf_dir / source.filename))
        for key, source in sources.items()
    }
    rows: list[dict[str, object]] = []

    basic_oct = parse_basic_silviculture(texts["silv_oct"], sources["silv_oct"], rows)
    basic_jul = parse_basic_silviculture(texts["silv_jul"], sources["silv_jul"], rows)
    fma = parse_iam_fma(read_tables(args.pdf_dir / sources["iam"].filename, 69), sources["iam"], rows)
    rail = parse_iam_rail(texts["iam"], sources["iam"], rows)
    specified_iam = parse_iam_specified(texts["iam"], sources["iam"], rows)
    subgrade = parse_iam_subgrade(texts["iam"], sources["iam"], rows)
    stabilizing = parse_iam_stabilizing(texts["iam"], sources["iam"], rows)
    culverts_iam = parse_iam_culverts(
        read_tables(args.pdf_dir / sources["iam"].filename, 83), sources["iam"], rows
    )
    bridges_iam = parse_iam_bridge(texts["iam"], sources["iam"], rows)
    road_maint = parse_iam_road_maintenance(
        read_tables(args.pdf_dir / sources["iam"].filename, 95), sources["iam"], rows
    )
    bec = parse_iam_bec(texts["iam"], sources["iam"], rows)
    toa_iam = parse_iam_toa_parameters(texts["iam"], sources["iam"], rows)

    road_cam = parse_cam_road(texts["cam"], sources["cam"], rows)
    log_bridge = parse_cam_log_bridge(texts["cam"], sources["cam"], rows)
    permanent_bridge = parse_cam_permanent_bridge(texts["cam"], sources["cam"], rows)
    culverts_cam = parse_cam_culverts(texts["cam"], sources["cam"], rows)
    silv_cam = parse_cam_silviculture(texts["cam"], sources["cam"], rows)
    toa_cam = parse_cam_toa_parameters(texts["cam"], sources["cam"], rows)
    specified_cam = parse_cam_specified(texts["cam"], sources["cam"], rows)

    rows.sort(
        key=lambda row: (
            str(row["region"]),
            str(row["component_group"]),
            str(row["component"]),
            str(row["zone"]),
            str(row["road_group"]),
            str(row["matrix_row"]),
            str(row["matrix_column"]),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["record_id"] = f"bc_appraisal_costs_{index:06d}"

    coverage = coverage_rows()
    for record in coverage:
        record["extraction_status"] = record.pop("status")
    coverage_fields = COVERAGE_FIELDS[:-1] + ["extraction_status"]
    # Keep notes last for readability.
    coverage_fields = ["region", "table_or_section", "component_group", "extraction_status", "notes"]
    stats = validate(rows, coverage)
    checks = spot_checks(rows)

    out_dir = args.out_dir
    write_csv(out_dir / "bc_appraisal_costs.csv", rows, FIELDS)
    write_csv(out_dir / "bc_appraisal_coverage.csv", coverage, coverage_fields)

    source_records = [
        {
            "key": source.key,
            "label": source.label,
            "file": source.filename,
            "url": source.url,
            "effective_date": source.effective_date,
            "retrieved_utc": source.retrieved_utc,
            "sha256": source.sha256,
            "bytes": (args.pdf_dir / source.filename).stat().st_size,
            "pages": source.pages,
        }
        for source in sources.values()
    ]
    discovery_pages: list[dict[str, object]] = []
    pages_dir = args.pdf_dir.parent / "pages"
    page_urls = {
        "iam.html": (
            "https://www2.gov.bc.ca/gov/content/industry/forestry/"
            "competitive-forest-industry/timber-pricing/interior-timber-pricing/"
            "interior-appraisal-manual"
        ),
        "cam.html": (
            "https://www2.gov.bc.ca/gov/content/industry/forestry/"
            "competitive-forest-industry/timber-pricing/coast-timber-pricing/"
            "coast-appraisal-manual-and-amendments"
        ),
        "silv.html": (
            "https://www2.gov.bc.ca/gov/content/industry/forestry/"
            "competitive-forest-industry/timber-pricing/interior-timber-pricing/"
            "interior-basic-silviculture-costs"
        ),
    }
    for name, url in page_urls.items():
        path = pages_dir / name
        if not path.exists():
            continue
        discovery_pages.append(
            {
                "file": name,
                "url": url,
                "sha256": sha256_file(path),
                "retrieved_utc": datetime.fromtimestamp(
                    path.stat().st_mtime, UTC
                ).replace(microsecond=0).isoformat(),
                "bytes": path.stat().st_size,
            }
        )
    policy_rules = [
        "All values are appraisal allowances or tabular cost estimates, not observed contractor costs.",
        "IAM FMA equation result is clamped to the published regional minimum and maximum.",
        "IAM Road Group 1 subgrade and ballast are ECE, not tabular; snow/ice roads are $9,814/km.",
        "IAM/ CAM tabular bridge and culvert costs must not be interpolated; use nearest published unit.",
        "IAM FEWB floor $0.25/m3; CAM EWB and FEWB floor $0.25/m3.",
        "IAM high development cost: HDC = DC - 3.27, zero when DC <= 5.93 (BCTS only).",
        "CAM high development cost: HDC = DC - 8.23, zero when DC <= 13.76 (BCTS only).",
        "CAM long haul: LHC = (HD - 100) * 0.171, zero when HD <= 100 km.",
        "IAM surface tow: rate = 0.0389 * D - 0.8744 with a $1.69/m3 floor.",
        "IAM root disease control is clamped to $1,200-$1,469/ha; CAM to $1,224-$1,560/ha.",
        "CAM scale-based FPA and road management are multiplied by CTC 0.975; FTOA uses RFM 1.052.",
        "IAM FTOA uses RFM 1.076 and market logger MLDC $2.66/m3 plus MLSO $0.12/m3.",
        "Interior quarterly basic silviculture costs are published by forest zone, not BGC zone.",
        "BEC silviculture tables are $/ha; quarterly Interior basic silviculture is $/m3.",
        "Restricted ILCR/CCTMCS microdata were not used.",
    ]
    manifest = {
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "observation_type": OBSERVATION_TYPE,
        "description": (
            "BC public appraisal cost allowances from IAM 2026 (effective 2026-07-01), "
            "CAM 2026 (effective 2026-01-01), and Interior Basic Silviculture quarterly "
            "costs (2026-10-01 and 2026-07-01)."
        ),
        "sources": source_records,
        "discovery_pages": discovery_pages,
        "row_counts": {
            "total": len(rows),
            "basic_silviculture_oct_2026": basic_oct,
            "basic_silviculture_jul_2026": basic_jul,
            "iam_fma_regions": fma,
            "iam_railway_origins": rail,
            "iam_specified_operations": specified_iam,
            "iam_subgrade_rows": subgrade,
            "iam_stabilizing_rows": stabilizing,
            "iam_culvert_cells": culverts_iam,
            "iam_bridge_cells": bridges_iam,
            "iam_road_maintenance_groups": road_maint,
            "iam_bec_rows": bec,
            "iam_toa_rows": toa_iam,
            "cam_road_rows": road_cam,
            "cam_log_bridge_cells": log_bridge,
            "cam_permanent_bridge_cells": permanent_bridge,
            "cam_culvert_cells": culverts_cam,
            "cam_silviculture_rows": silv_cam,
            "cam_toa_rows": toa_cam,
            "cam_specified_operations": specified_cam,
        },
        "stats": stats,
        "policy_rules": policy_rules,
        "spot_checks": checks,
        "coverage": {
            "parsed": sum(1 for row in coverage if row["extraction_status"] == "parsed"),
            "parsed_formula": sum(
                1 for row in coverage if row["extraction_status"] == "parsed_formula"
            ),
            "partial": sum(1 for row in coverage if row["extraction_status"] == "partial"),
            "not_published": sum(
                1 for row in coverage if row["extraction_status"] == "not_published"
            ),
            "policy_parameters": sum(
                1 for row in coverage if "policy_parameter" in row["extraction_status"]
            ),
            "rules": sum(
                1 for row in coverage if "rule" in row["extraction_status"]
            ),
        },
        "caveats": [
            "Allowances are appraisal inputs, not winning bids, contractor costs, or observed transactions.",
            "The IAM and CAM do not publish a general tree-to-truck or logging $/m3 allowance table.",
            "General truck hauling is represented only by cycle-time/EWB policy and by rail/water/barge specified operations.",
            "ECE project costs are site-specific; only tabular components, trend factors, and policy are captured.",
            "Bridge and culvert values are per structure or per metre, not per m3 of timber.",
            "The quarterly Interior table is by forest zone; BGC detail exists only in IAM Table 4-8 ($/ha).",
        ],
        "build_command": (
            "python scripts/national/build_bc_appraisal_costs.py "
            "--pdf-dir tmp/bc_appraisal_costs_raw/pdfs "
            "--out-dir data/derived/national/harvest_basis/bc_appraisal_costs"
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"rows": len(rows), "coverage": len(coverage)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
