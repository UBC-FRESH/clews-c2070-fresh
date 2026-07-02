#!/usr/bin/env python3
"""Summarize generated FCM LP files without committing bulky model text."""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


DEFAULT_LP_FILES = [
    Path("tmp/FCM/Report/log_residue/Logflow_model.lp"),
    Path("tmp/FCM/Report/log_residue/ResidueFlow_model.lp"),
    Path("tmp/FCM/Report/testFCM2/FCM_model.lp"),
    Path("tmp/FCM/Carbon/Test_runs/Basecase-20190514/Log_allocation_model.lp"),
    Path("tmp/FCM/Carbon/Test_runs/Basecase-20190514/Secondary_Log_and_Residue_allocation_model.lp"),
]

TERM_RE = re.compile(
    r"(?P<sign>[+-]?)\s*(?P<coef>(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s+"
    r"(?P<var>[A-Za-z_][A-Za-z0-9_#-]*)"
)
CONSTRAINT_RE = re.compile(r"^\s*c\d+:")
SENSE_RE = re.compile(r"\s(<=|>=|=)\s")


def read_commodities(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted([row["commodity"] for row in rows], key=len, reverse=True)


def strip_var_id(name: str) -> str:
    return name.split("#", 1)[0]


def split_variable(name: str, commodities: list[str]) -> tuple[str, str, str, str]:
    base = strip_var_id(name)
    for commodity in commodities:
        delimiter = f"{commodity}_"
        delimiter_index = base.find(delimiter)
        if delimiter_index != -1:
            source_node = base[:delimiter_index]
            source_commodity = commodity
            right = base[delimiter_index + len(delimiter) :]
            break
    else:
        if "_" not in base:
            return "", "", "", ""
        left, right = base.split("_", 1)
        source_commodity = ""
        source_node = left
    product = ""
    destination = right
    for commodity in commodities:
        if right.endswith(commodity):
            product = commodity
            destination = right[: -len(commodity)]
            break
    return source_node, source_commodity, destination, product


def classify_node(node: str) -> str:
    if not node:
        return "unknown"
    if node.startswith("export_"):
        return "export_sink"
    if node.startswith("import_"):
        return "import_source"
    if re.match(r"^[A-Z]{2,3}\d+$", node):
        return "fmu"
    match = re.match(r"^[A-Z]{2}m([A-Z]+)m", node)
    if match:
        return match.group(1)
    if node.startswith("Sawmill"):
        return "SAW"
    if node.startswith("FMA"):
        return "fmu"
    if node.startswith("Elec"):
        return "BIO"
    if node.startswith("Pellet"):
        return "PEL"
    return "network_node"


def iter_sections(path: Path):
    section = "header"
    with path.open(encoding="ISO-8859-1", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if stripped == "Maximize":
                section = "objective"
                continue
            if stripped == "Subject To":
                section = "constraints"
                continue
            if stripped in {"Bounds", "Binaries", "Generals", "End"}:
                section = stripped.lower()
            yield section, line.rstrip("\n")


def objective_terms(path: Path) -> list[tuple[float, str]]:
    terms: list[tuple[float, str]] = []
    for section, line in iter_sections(path):
        if section != "objective":
            continue
        if line.strip().startswith("obj:"):
            line = line.split("obj:", 1)[1]
        for match in TERM_RE.finditer(line):
            sign = -1.0 if match.group("sign") == "-" else 1.0
            terms.append((sign * float(match.group("coef")), match.group("var")))
    return terms


def constraint_senses(path: Path) -> tuple[int, Counter[str]]:
    count = 0
    senses: Counter[str] = Counter()
    buffer = ""
    for section, line in iter_sections(path):
        if section != "constraints":
            continue
        if CONSTRAINT_RE.match(line):
            if buffer:
                sense = SENSE_RE.search(buffer)
                senses[sense.group(1) if sense else "unknown"] += 1
            count += 1
            buffer = line
        else:
            buffer += " " + line.strip()
    if buffer:
        sense = SENSE_RE.search(buffer)
        senses[sense.group(1) if sense else "unknown"] += 1
    return count, senses


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize_file(path: Path, commodities: list[str]) -> tuple[dict[str, str], list[dict[str, str]]]:
    terms = objective_terms(path)
    variables = [var for _, var in terms]
    unique_variables = sorted(set(variables))
    coeffs = [coef for coef, _ in terms]
    constraint_count, senses = constraint_senses(path)
    source_commodities: Counter[str] = Counter()
    products: Counter[str] = Counter()
    source_classes: Counter[str] = Counter()
    destination_classes: Counter[str] = Counter()
    family_rows: list[dict[str, str]] = []
    family_counter: Counter[tuple[str, str, str, str]] = Counter()
    for var in unique_variables:
        source, source_commodity, destination, product = split_variable(var, commodities)
        source_class = classify_node(source)
        destination_class = classify_node(destination)
        source_commodities[source_commodity or "unknown"] += 1
        products[product or "unknown"] += 1
        source_classes[source_class] += 1
        destination_classes[destination_class] += 1
        family_counter[(source_class, source_commodity or "unknown", destination_class, product or "unknown")] += 1
    for (source_class, source_commodity, destination_class, product), count in family_counter.most_common():
        family_rows.append(
            {
                "lp_file": str(path),
                "source_class": source_class,
                "source_commodity": source_commodity,
                "destination_class": destination_class,
                "product": product,
                "variable_count": str(count),
            }
        )
    summary = {
        "lp_file": str(path),
        "line_count": str(sum(1 for _ in path.open(encoding="ISO-8859-1", errors="replace"))),
        "objective_term_count": str(len(terms)),
        "unique_variable_count": str(len(unique_variables)),
        "constraint_count": str(constraint_count),
        "constraint_senses": ";".join(f"{k}:{v}" for k, v in senses.most_common()),
        "min_objective_coefficient": f"{min(coeffs):.12g}" if coeffs else "",
        "max_objective_coefficient": f"{max(coeffs):.12g}" if coeffs else "",
        "negative_objective_terms": str(sum(1 for coef in coeffs if coef < 0)),
        "positive_objective_terms": str(sum(1 for coef in coeffs if coef > 0)),
        "source_classes": ";".join(f"{k}:{v}" for k, v in source_classes.most_common()),
        "destination_classes": ";".join(f"{k}:{v}" for k, v in destination_classes.most_common()),
        "source_commodities_top20": ";".join(f"{k}:{v}" for k, v in source_commodities.most_common(20)),
        "products_top20": ";".join(f"{k}:{v}" for k, v in products.most_common(20)),
    }
    return summary, family_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("metadata/ffcm-archive/lp-forensics"))
    parser.add_argument("--commodities", type=Path, default=Path("metadata/ffcm-archive/parsed/commodities.csv"))
    parser.add_argument("lp_files", nargs="*", type=Path)
    args = parser.parse_args()

    lp_files = args.lp_files or DEFAULT_LP_FILES
    commodities = read_commodities(args.commodities)
    summaries: list[dict[str, str]] = []
    families: list[dict[str, str]] = []
    for path in lp_files:
        summary, family_rows = summarize_file(path, commodities)
        summaries.append(summary)
        families.extend(family_rows)
    write_csv(
        args.out_dir / "lp-model-summary.csv",
        summaries,
        [
            "lp_file",
            "line_count",
            "objective_term_count",
            "unique_variable_count",
            "constraint_count",
            "constraint_senses",
            "min_objective_coefficient",
            "max_objective_coefficient",
            "negative_objective_terms",
            "positive_objective_terms",
            "source_classes",
            "destination_classes",
            "source_commodities_top20",
            "products_top20",
        ],
    )
    write_csv(
        args.out_dir / "lp-variable-families.csv",
        families,
        ["lp_file", "source_class", "source_commodity", "destination_class", "product", "variable_count"],
    )


if __name__ == "__main__":
    main()
