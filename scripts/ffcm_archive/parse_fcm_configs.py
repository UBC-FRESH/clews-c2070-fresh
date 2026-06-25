#!/usr/bin/env python3
"""Parse selected FCM configuration files into lightweight CSV evidence tables."""

from __future__ import annotations

import argparse
import csv
import re
import shlex
from pathlib import Path
from collections import Counter


BASECASE = Path("tmp/FCM/Carbon/01-Basecase")
INCLUDES = Path("tmp/FCM/Carbon/02-Inc")
SCENARIOS = Path("tmp/FCM/Carbon/03-Scenarios")

CORE_FILES = [
    BASECASE / "FMUs.cfg",
    BASECASE / "Facilities.cfg",
    BASECASE / "Routes.cfg",
    BASECASE / "Transporters.cfg",
    BASECASE / "Processinglines.cfg",
    BASECASE / "Markets.cfg",
    BASECASE / "Imports.cfg",
    BASECASE / "Transload.cfg",
    BASECASE / "Groups.cfg",
    BASECASE / "DefinedEntities.cfg",
    BASECASE / "MutuallyExclusive_Processinglines.cfg",
    INCLUDES / "Commodities.inc",
    INCLUDES / "Constraints.inc",
    INCLUDES / "Throughput.inc",
    SCENARIOS / "Basecase.cfg",
]


NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
BRACKET_QUANTITY_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\[([^\]]+)\]$"
)


def tokenize(text: str) -> list[str]:
    return shlex.split(text.replace("\u00a0", " "), posix=True)


def parse_top_blocks(line: str) -> tuple[str, list[tuple[str, str]]]:
    line = line.strip()
    if not line:
        return "", []
    parts = line.split(None, 1)
    entity = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    blocks: list[tuple[str, str]] = []
    i = 0
    while i < len(rest):
        while i < len(rest) and rest[i].isspace():
            i += 1
        if i >= len(rest):
            break
        brace = rest.find("{", i)
        if brace == -1:
            break
        attr = rest[i:brace].strip()
        depth = 0
        start = brace + 1
        j = brace
        while j < len(rest):
            if rest[j] == "{":
                depth += 1
            elif rest[j] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((attr, rest[start:j].strip()))
                    i = j + 1
                    break
            j += 1
        else:
            blocks.append((attr, rest[start:].strip()))
            break
    return entity, blocks


def inner_groups(text: str) -> list[str]:
    groups: list[str] = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                groups.append(text[start:i].strip())
                start = None
    return groups


def parse_numeric_unit(tokens: list[str], start: int = 0) -> tuple[str, str]:
    if start >= len(tokens):
        return "", ""
    first = tokens[start]
    bracketed = BRACKET_QUANTITY_RE.match(first)
    if bracketed:
        return bracketed.group(1), bracketed.group(2)
    value = first
    unit = " ".join(tokens[start + 1 :])
    return value, unit


def parse_named_quantity(group: str) -> tuple[str, str, str]:
    tokens = tokenize(group)
    if not tokens:
        return "", "", ""
    name = tokens[0]
    value, unit = parse_numeric_unit(tokens, 1)
    return name, value, unit


def parse_quantity(text: str) -> tuple[str, str]:
    return parse_numeric_unit(tokenize(text), 0)


def first_token(text: str) -> str:
    tokens = tokenize(text)
    return tokens[0] if tokens else ""


def classify_facility(entity: str) -> str:
    if re.match(r"^[A-Z]{2}-[A-Z]+-", entity):
        return entity.split("-")[1]
    if entity.startswith("TL"):
        return "transport_link_node"
    if entity.startswith("W"):
        return "water_node"
    if entity.startswith("import_"):
        return "import_source"
    if entity.startswith("export_"):
        return "export_sink"
    return "network_node"


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def iter_records(files: list[Path]):
    for source_file in files:
        with source_file.open(encoding="utf-8", errors="replace") as f:
            for line_number, line in enumerate(f, start=1):
                entity, blocks = parse_top_blocks(line)
                if not entity:
                    continue
                yield source_file, line_number, entity, blocks


def build_raw_records(files: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records(files):
        for attr, value in blocks:
            rows.append(
                {
                    "source_file": str(source_file),
                    "line_number": str(line_number),
                    "entity": entity,
                    "attribute": attr,
                    "value_text": value,
                }
            )
    return rows


def build_supply_rows(path: Path, entity_col: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        for attr, value in blocks:
            if attr not in {"InitialContent", "SupplyCost", "MinimumSupply", "OperatingCost"}:
                continue
            for group in inner_groups(value):
                commodity, amount, unit = parse_named_quantity(group)
                rows.append(
                    {
                        "source_file": str(source_file),
                        "line_number": str(line_number),
                        entity_col: entity,
                        "attribute": attr,
                        "commodity": commodity,
                        "value": amount,
                        "unit": unit,
                    }
                )
    return rows


def build_facilities(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    facilities: list[dict[str, str]] = []
    process_groups: list[dict[str, str]] = []
    minimums: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        attrs = {attr: value for attr, value in blocks}
        transporters = ";".join(tokenize(attrs.get("Transporters", "")))
        facilities.append(
            {
                "source_file": str(source_file),
                "line_number": str(line_number),
                "facility": entity,
                "facility_class": classify_facility(entity),
                "transporters": transporters,
            }
        )
        for group_index, group in enumerate(inner_groups(attrs.get("MutuallyExclusiveProcesses", "")), start=1):
            for process_line in tokenize(group):
                process_groups.append(
                    {
                        "source_file": str(source_file),
                        "line_number": str(line_number),
                        "facility": entity,
                        "group_index": str(group_index),
                        "processing_line": process_line,
                    }
                )
        for group in inner_groups(attrs.get("MinimumProduction", "")):
            commodity, amount, unit = parse_named_quantity(group)
            minimums.append(
                {
                    "source_file": str(source_file),
                    "line_number": str(line_number),
                    "facility": entity,
                    "commodity": commodity,
                    "minimum_production": amount,
                    "unit": unit,
                }
            )
    return facilities, process_groups, minimums


def build_processing_lines(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    lines: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        attrs = {attr: value for attr, value in blocks}
        infeed_value, infeed_unit = parse_quantity(attrs.get("InfeedRate", ""))
        op_value, op_unit = parse_quantity(attrs.get("OperatingCost", ""))
        lines.append(
            {
                "source_file": str(source_file),
                "line_number": str(line_number),
                "processing_line": entity,
                "facility": first_token(attrs.get("Facility", "")),
                "feedstock": first_token(attrs.get("Feedstock", "")),
                "primary_product": first_token(attrs.get("PrimaryProduct", "")),
                "infeed_rate": infeed_value,
                "infeed_unit": infeed_unit,
                "throughput": first_token(attrs.get("Throughput", "")),
                "operating_cost": op_value,
                "operating_cost_unit": op_unit,
            }
        )
        for attr in ("OutfeedRateByEntity", "AdditionalInfeed"):
            for group in inner_groups(attrs.get(attr, "")):
                commodity, amount, unit = parse_named_quantity(group)
                outputs.append(
                    {
                        "source_file": str(source_file),
                        "line_number": str(line_number),
                        "processing_line": entity,
                        "flow_attribute": attr,
                        "commodity": commodity,
                        "value": amount,
                        "unit": unit,
                    }
                )
    return lines, outputs


def build_routes(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        attrs = {attr: value for attr, value in blocks}
        origins = tokenize(attrs.get("OriginList", ""))
        destinations = tokenize(attrs.get("DestinationList", ""))
        segments = tokenize(attrs.get("Segments", ""))
        lengths = tokenize(attrs.get("LengthList", ""))
        speeds = tokenize(attrs.get("SpeedLimitList", ""))
        length_unit = lengths[-1] if lengths else ""
        speed_unit = speeds[-1] if speeds else ""
        length_values = lengths[:-1] if lengths and not NUMBER_RE.match(lengths[-1]) else lengths
        speed_values = speeds[:-1] if speeds and not NUMBER_RE.match(speeds[-1]) else speeds
        total_length = 0.0
        for value in length_values:
            if NUMBER_RE.match(value):
                total_length += float(value)
        rows.append(
            {
                "source_file": str(source_file),
                "line_number": str(line_number),
                "route": entity,
                "origin_list": ";".join(origins),
                "destination_list": ";".join(destinations),
                "segments": ";".join(segments),
                "lengths": ";".join(length_values),
                "length_unit": length_unit,
                "total_length": f"{total_length:.12g}",
                "speed_limits": ";".join(speed_values),
                "speed_unit": speed_unit,
                "transport_mode": first_token(attrs.get("TransportMode", "")),
                "handles": ";".join(tokenize(attrs.get("Handles", ""))),
                "segment_count": str(max(len(segments), len(length_values), len(speed_values))),
            }
        )
    return rows


def build_transporters(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    materials: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        attrs = {attr: value for attr, value in blocks}
        speed_value, speed_unit = parse_quantity(attrs.get("CruisingSpeed", ""))
        operating_value, operating_unit = parse_quantity(attrs.get("OperatingCost", ""))
        fuel_value, fuel_unit = parse_quantity(attrs.get("FuelCost", ""))
        rows.append(
            {
                "source_file": str(source_file),
                "line_number": str(line_number),
                "transporter": entity,
                "cruising_speed": speed_value,
                "cruising_speed_unit": speed_unit,
                "transport_mode": first_token(attrs.get("TransportMode", "")),
                "operating_cost": operating_value,
                "operating_cost_unit": operating_unit,
                "fuel_cost": fuel_value,
                "fuel_cost_unit": fuel_unit,
            }
        )
        for group in inner_groups(attrs.get("AcceptingMaterial", "")):
            commodity, amount, unit = parse_named_quantity(group)
            materials.append(
                {
                    "source_file": str(source_file),
                    "line_number": str(line_number),
                    "transporter": entity,
                    "commodity": commodity,
                    "capacity": amount,
                    "unit": unit,
                }
            )
    return rows, materials


def build_commodities(path: Path) -> list[dict[str, str]]:
    price_unit_type: dict[str, str] = {}
    price_values: dict[str, tuple[str, str, str]] = {}
    commodity_rows: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        attrs = {attr: value for attr, value in blocks}
        if "UnitType" in attrs and "Value" in attrs:
            price_unit_type[entity] = first_token(attrs["UnitType"])
            groups = inner_groups(attrs["Value"])
            if groups:
                tokens = tokenize(groups[0])
                period = tokens[0] if len(tokens) > 0 else ""
                year_unit = tokens[1] if len(tokens) > 1 else ""
                value, unit = parse_numeric_unit(tokens, 2)
                price_values[entity] = (period, year_unit, value, unit)
            continue
        if "MatterState" in attrs:
            price_ref = first_token(attrs.get("Price", ""))
            density_value, density_unit = parse_quantity(attrs.get("Density", ""))
            energy_value, energy_unit = parse_quantity(attrs.get("EnergyDensity", ""))
            period, year_unit, price_value, price_unit = price_values.get(price_ref, ("", "", "", ""))
            commodity_rows.append(
                {
                    "source_file": str(source_file),
                    "line_number": str(line_number),
                    "commodity": entity,
                    "matter_state": first_token(attrs.get("MatterState", "")),
                    "price_ref": price_ref,
                    "price_unit_type": price_unit_type.get(price_ref, ""),
                    "price_period": period,
                    "price_period_unit": year_unit,
                    "price_value": price_value,
                    "price_unit": price_unit,
                    "density": density_value,
                    "density_unit": density_unit,
                    "energy_density": energy_value,
                    "energy_density_unit": energy_unit,
                }
            )
    return commodity_rows


def build_markets(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    markets: list[dict[str, str]] = []
    reports: list[dict[str, str]] = []
    for source_file, line_number, entity, blocks in iter_records([path]):
        attrs = {attr: value for attr, value in blocks}
        if "Product" in attrs:
            markets.append(
                {
                    "source_file": str(source_file),
                    "line_number": str(line_number),
                    "market": entity,
                    "product": first_token(attrs.get("Product", "")),
                    "sellers": ";".join(tokenize(attrs.get("Sellers", ""))),
                    "buyers": ";".join(tokenize(attrs.get("Buyers", ""))),
                }
            )
        for attr in ("ConcurrentMarkets", "SupplyGroupsToReport", "BuyerGroupsToReport"):
            if attr in attrs:
                reports.append(
                    {
                        "source_file": str(source_file),
                        "line_number": str(line_number),
                        "entity": entity,
                        "attribute": attr,
                        "members": ";".join(tokenize(attrs[attr])),
                    }
                )
    return markets, reports


def write_parse_summary(
    out_dir: Path,
    facilities: list[dict[str, str]],
    processing_lines: list[dict[str, str]],
    processing_outputs: list[dict[str, str]],
    commodities: list[dict[str, str]],
    fmu_supply: list[dict[str, str]],
    routes: list[dict[str, str]],
    transporters: list[dict[str, str]],
    markets: list[dict[str, str]],
    import_supply: list[dict[str, str]],
) -> None:
    fmu_ids = {row["fmu"] for row in fmu_supply}
    route_ids = {row["route"] for row in routes}
    route_segment_count = sum(int(row["segment_count"] or "0") for row in routes)
    import_ids = {row["import_source"] for row in import_supply}
    rows = [
        {"metric": "fmu_count", "value": str(len(fmu_ids)), "note": "Unique FMU supply entities in FMUs.cfg."},
        {"metric": "fmu_supply_rows", "value": str(len(fmu_supply)), "note": "Long-form FMU InitialContent, SupplyCost, and MinimumSupply rows."},
        {"metric": "facility_count", "value": str(len(facilities)), "note": "Entities declared in Facilities.cfg, including mills and network nodes."},
        {
            "metric": "facility_classes",
            "value": ";".join(f"{k}:{v}" for k, v in Counter(row["facility_class"] for row in facilities).most_common()),
            "note": "Facility.cfg class counts inferred from entity names.",
        },
        {"metric": "processing_line_count", "value": str(len(processing_lines)), "note": "Processing line definitions in Processinglines.cfg."},
        {
            "metric": "processing_line_feedstocks",
            "value": ";".join(f"{k}:{v}" for k, v in Counter(row["feedstock"] for row in processing_lines).most_common()),
            "note": "Feedstock classes accepted by processing lines.",
        },
        {
            "metric": "processing_output_commodities_top20",
            "value": ";".join(f"{k}:{v}" for k, v in Counter(row["commodity"] for row in processing_outputs).most_common(20)),
            "note": "Most common outfeed/additional-infeed commodities across processing lines.",
        },
        {"metric": "commodity_count", "value": str(len(commodities)), "note": "Commodity entities in Commodities.inc."},
        {
            "metric": "commodity_matter_states",
            "value": ";".join(f"{k}:{v}" for k, v in Counter(row["matter_state"] for row in commodities).most_common()),
            "note": "Matter-state classes used by commodities.",
        },
        {"metric": "route_count", "value": str(len(route_ids)), "note": "Unique route entities in Routes.cfg."},
        {"metric": "route_segment_count", "value": str(route_segment_count), "note": "Total route segment classes encoded across route definitions."},
        {
            "metric": "route_modes",
            "value": ";".join(f"{k}:{v}" for k, v in Counter(row["transport_mode"] for row in routes).most_common()),
            "note": "Route definitions by transport mode.",
        },
        {"metric": "transporter_count", "value": str(len(transporters)), "note": "Transporter definitions in Transporters.cfg."},
        {
            "metric": "transporter_modes",
            "value": ";".join(f"{k}:{v}" for k, v in Counter(row["transport_mode"] for row in transporters).most_common()),
            "note": "Transporter definitions by mode.",
        },
        {"metric": "market_count", "value": str(len(markets)), "note": "Explicit product markets in Markets.cfg."},
        {"metric": "market_names", "value": ";".join(row["market"] for row in markets), "note": "Named market modules."},
        {"metric": "import_source_count", "value": str(len(import_ids)), "note": "Unique import sources in Imports.cfg."},
    ]
    write_csv(out_dir / "parse-summary.csv", rows, ["metric", "value", "note"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("metadata/ffcm-archive/parsed"))
    parser.add_argument("--include-raw", action="store_true", help="Also write the large raw_config_attributes.csv evidence dump.")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.include_raw:
        write_csv(
            args.out_dir / "raw_config_attributes.csv",
            build_raw_records(CORE_FILES),
            ["source_file", "line_number", "entity", "attribute", "value_text"],
        )
    fmu_supply = build_supply_rows(BASECASE / "FMUs.cfg", "fmu")
    write_csv(
        args.out_dir / "fmu_supply.csv",
        fmu_supply,
        ["source_file", "line_number", "fmu", "attribute", "commodity", "value", "unit"],
    )
    import_supply = build_supply_rows(BASECASE / "Imports.cfg", "import_source")
    write_csv(
        args.out_dir / "import_supply.csv",
        import_supply,
        ["source_file", "line_number", "import_source", "attribute", "commodity", "value", "unit"],
    )
    write_csv(
        args.out_dir / "transload_costs.csv",
        build_supply_rows(BASECASE / "Transload.cfg", "transload_node"),
        ["source_file", "line_number", "transload_node", "attribute", "commodity", "value", "unit"],
    )

    facilities, process_groups, minimums = build_facilities(BASECASE / "Facilities.cfg")
    write_csv(args.out_dir / "facilities.csv", facilities, ["source_file", "line_number", "facility", "facility_class", "transporters"])
    write_csv(
        args.out_dir / "facility_process_groups.csv",
        process_groups,
        ["source_file", "line_number", "facility", "group_index", "processing_line"],
    )
    write_csv(
        args.out_dir / "facility_minimum_production.csv",
        minimums,
        ["source_file", "line_number", "facility", "commodity", "minimum_production", "unit"],
    )

    processing_lines, processing_outputs = build_processing_lines(BASECASE / "Processinglines.cfg")
    write_csv(
        args.out_dir / "processing_lines.csv",
        processing_lines,
        [
            "source_file",
            "line_number",
            "processing_line",
            "facility",
            "feedstock",
            "primary_product",
            "infeed_rate",
            "infeed_unit",
            "throughput",
            "operating_cost",
            "operating_cost_unit",
        ],
    )
    write_csv(
        args.out_dir / "processing_line_flows.csv",
        processing_outputs,
        ["source_file", "line_number", "processing_line", "flow_attribute", "commodity", "value", "unit"],
    )

    route_segments = build_routes(BASECASE / "Routes.cfg")
    write_csv(
        args.out_dir / "routes.csv",
        route_segments,
        [
            "source_file",
            "line_number",
            "route",
            "origin_list",
            "destination_list",
            "segments",
            "lengths",
            "length_unit",
            "total_length",
            "speed_limits",
            "speed_unit",
            "transport_mode",
            "handles",
            "segment_count",
        ],
    )

    transporters, transporter_materials = build_transporters(BASECASE / "Transporters.cfg")
    write_csv(
        args.out_dir / "transporters.csv",
        transporters,
        [
            "source_file",
            "line_number",
            "transporter",
            "cruising_speed",
            "cruising_speed_unit",
            "transport_mode",
            "operating_cost",
            "operating_cost_unit",
            "fuel_cost",
            "fuel_cost_unit",
        ],
    )
    write_csv(
        args.out_dir / "transporter_materials.csv",
        transporter_materials,
        ["source_file", "line_number", "transporter", "commodity", "capacity", "unit"],
    )

    commodities = build_commodities(INCLUDES / "Commodities.inc")
    write_csv(
        args.out_dir / "commodities.csv",
        commodities,
        [
            "source_file",
            "line_number",
            "commodity",
            "matter_state",
            "price_ref",
            "price_unit_type",
            "price_period",
            "price_period_unit",
            "price_value",
            "price_unit",
            "density",
            "density_unit",
            "energy_density",
            "energy_density_unit",
        ],
    )

    markets, market_reports = build_markets(BASECASE / "Markets.cfg")
    write_csv(args.out_dir / "markets.csv", markets, ["source_file", "line_number", "market", "product", "sellers", "buyers"])
    write_csv(
        args.out_dir / "market_reporting_groups.csv",
        market_reports,
        ["source_file", "line_number", "entity", "attribute", "members"],
    )
    write_parse_summary(
        args.out_dir,
        facilities,
        processing_lines,
        processing_outputs,
        commodities,
        fmu_supply,
        route_segments,
        transporters,
        markets,
        import_supply,
    )


if __name__ == "__main__":
    main()
