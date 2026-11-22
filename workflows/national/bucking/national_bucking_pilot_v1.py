"""National bucking pilot v1 across 13 Canadian jurisdictions.

Scope
-----
This is a **representative synthetic pilot**, not inventory and not a national
supply estimate.  It builds ten deterministic representative trees per
province from open NFI coverage composition and fitted DBH/height statistics,
dispatches a published taper per jurisdiction, applies one national
dimension-only pilot sort set, and reconciles recovered logs against a
residue ledger.

Taper dispatch
--------------
* Quebec: Fortin, Schneider and Saucier (2013) fixed-effect tree taper.
* British Columbia: Kozak (2004) Version 6 parameter table via ``nemora``.
* Other provinces: the BC Kozak or Quebec Schneider published taper is used
  as a documented cross-jurisdiction donor and labelled ``donor_published``.

Every geometry row is ``synthetic``/``model_estimate``; every product row is
``observed=false`` and ``policy_grade=false``.  No official log grade is
assigned: ``grade=not_assigned`` and ``sort_basis=dimension_only``.

Volume balance is explicit.  Residue is ``taper total - recovered gross log
volume`` per stem, so the stump section above the 0.3 m taper base, top and
unmerchantable material are visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from nemora.workflows.taper import (
    KOZAK_SOURCE,
    KOZAK_SPECIES,
    diameter_inside_bark,
    load_kozak_parameters,
    params_for,
)

from .pilot_schema import (
    EVIDENCE_TIERS,
    PilotBuckedLog,
    PilotResidueLedger,
    PilotTree,
    SchemaError,
    weakest_evidence,
)
from .schneider2013 import (
    DEFAULT_STUMP_HEIGHT_M,
    SchneiderTaperRegistry,
    load_taper_registry,
)
from .taper import default_kozak_parameter_path

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "PROVINCES",
    "assert_no_unexpected_deletions",
    "buck_tree",
    "build_representative_tree_lists",
    "generate_pilot",
    "load_national_sort_rules",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "national_bucking_pilot_v1"
)
QC_TAPER_PATH = (
    REPO_ROOT
    / "workflows"
    / "national"
    / "bucking"
    / "rules"
    / "qc_schneider2013_taper.csv"
)
SORT_RULES_PATH = (
    REPO_ROOT
    / "workflows"
    / "national"
    / "bucking"
    / "rules"
    / "national_pilot_sorts.csv"
)
NFI_COVERAGE_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "assortments"
    / "national_coverage"
    / "coverage.csv"
)
NFI_HEIGHT_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "assortments"
    / "national_fit"
    / "national_height_models.csv"
)
FACILITY_CHAIN_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "facilities"
    / "facility_product_chain_v1"
    / "province_product_chain.csv"
)

PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
PROVINCE_NAMES: dict[str, str] = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland and Labrador",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}
N_TREES_PER_PROVINCE = 10
MAX_SPECIES_PER_PROVINCE = 7
TARGET_SPECIES_COVERAGE = 0.95
MOISTURE_WET_BASIS = 0.45
CHIP_YIELD_FRACTION = 0.85
HOG_YIELD_FRACTION = 0.10
PRICE_DECK_ID = "NAT-PILOT-2026"
STUMP_HEIGHT_M = DEFAULT_STUMP_HEIGHT_M

#: Which published taper is the source for each non-local jurisdiction.
DONOR_POLICY: dict[str, str] = {
    "AB": "BC",
    "SK": "BC",
    "MB": "BC",
    "ON": "QC",
    "NB": "QC",
    "NS": "QC",
    "PE": "QC",
    "NL": "QC",
    "YT": "BC",
    "NT": "BC",
    "NU": "BC",
}

#: BEC anchor used for the BC Kozak donor.  These are BC ecological anchors,
#: not local BEC zones in the receiving province.
BEC_ANCHOR: dict[str, str] = {
    "PL": "SBS",
    "FD": "IDF",
    "HW": "CWH",
    "C": "CWH",
    "SW": "SBS",
    "BL": "ESSF",
    "AT": "BWBS",
    "L": "BWBS",
    "EP": "BWBS",
    "BA": "SBS",
    "S": "SBS",
    "SE": "ESSF",
    "B": "BWBS",
    "PW": "SBS",
    "AC": "SBS",
}

DECIDUOUS_CODES: frozenset[str] = frozenset(
    {"AT", "B", "E", "EP", "AC", "MB", "PA", "BOP", "PEG", "PET"}
)

#: Rounded typical basic densities (ODT/m3), planning assumptions only.
BASIC_DENSITY_ODT_M3: dict[str, float] = {
    "BOP": 0.50,
    "EPB": 0.37,
    "EPN": 0.42,
    "EPR": 0.38,
    "PEG": 0.35,
    "PET": 0.35,
    "SAB": 0.34,
    "THO": 0.29,
    "PL": 0.43,
    "FD": 0.45,
    "HW": 0.42,
    "C": 0.33,
    "SW": 0.37,
    "BL": 0.32,
    "AT": 0.35,
    "L": 0.49,
    "EP": 0.50,
    "BA": 0.32,
    "S": 0.37,
    "SE": 0.36,
    "B": 0.34,
    "PW": 0.35,
    "AC": 0.49,
    "MB": 0.45,
    "PA": 0.35,
}

#: NFI coverage species -> taper species.  The mapping is deliberately
#: explicit; unsupported species are excluded and reported in the manifest.
NFI_TO_BC: dict[str, str] = {
    "PINU.CON": "PL",
    "PINU.BAN": "PL",
    "PSEU.MEN": "FD",
    "TSUG.HET": "HW",
    "THUJ.PLI": "C",
    "THUJ.OCC": "C",
    "PICE.GLA": "SW",
    "PICE.MAR": "S",
    "PICE.RUB": "S",
    "PICE.SIT": "S",
    "PICE.ABI": "S",
    "PICE.ENG": "SE",
    "ABIE.LAS": "BL",
    "ABIE.BAL": "BA",
    "ABIE.AMA": "BA",
    "POPU.TRE": "AT",
    "POPU.BAL": "B",
    "LARI.OCC": "L",
    "LARI.LAR": "L",
    "BETU.PAP": "EP",
    "BETU.ALL": "EP",
    "PINU.RES": "PW",
    "PINU.STR": "PW",
}

NFI_TO_QC: dict[str, str] = {
    "ABIE.BAL": "SAB",
    "PICE.MAR": "EPN",
    "PICE.GLA": "EPB",
    "PICE.RUB": "EPR",
    "PICE.ENG": "EPB",
    "BETU.PAP": "BOP",
    "BETU.ALL": "BOP",
    "POPU.TRE": "PET",
    "POPU.BAL": "PET",
    "THUJ.PLI": "THO",
    "THUJ.OCC": "THO",
}

#: Documented composition assumptions for provinces without provincial NFI
#: coverage in the open coverage table.  Values are share of merchantable
#: stems, not area; they are explicit pilot assumptions.
ASSUMED_COMPOSITION: dict[str, tuple[tuple[str, float], ...]] = {
    "AB": (
        ("PICE.GLA", 0.32),
        ("POPU.TRE", 0.27),
        ("PICE.MAR", 0.16),
        ("PINU.CON", 0.10),
        ("ABIE.LAS", 0.07),
        ("POPU.BAL", 0.05),
        ("LARI.LAR", 0.03),
    ),
    "ON": (
        ("PICE.MAR", 0.28),
        ("ABIE.BAL", 0.22),
        ("POPU.TRE", 0.15),
        ("BETU.PAP", 0.13),
        ("PICE.GLA", 0.10),
        ("THUJ.OCC", 0.06),
        ("PICE.RUB", 0.06),
    ),
    "NS": (
        ("ABIE.BAL", 0.30),
        ("PICE.MAR", 0.22),
        ("BETU.PAP", 0.15),
        ("PICE.RUB", 0.10),
        ("POPU.TRE", 0.08),
        ("PICE.GLA", 0.07),
        ("THUJ.OCC", 0.08),
    ),
    "NU": (
        ("PICE.MAR", 0.45),
        ("PICE.GLA", 0.25),
        ("POPU.TRE", 0.15),
        ("BETU.PAP", 0.10),
        ("LARI.LAR", 0.05),
    ),
}

ASSUMPTION_TEXT: dict[str, str] = {
    "AB": "documented boreal mixedwood composition for a representative Alberta merchantable stand",
    "ON": "documented northern Ontario boreal-Great Lakes commercial conifer/hardwood mix; tolerant hardwoods excluded because no local taper is available",
    "NS": "documented Acadian mixedwood composition for a representative Nova Scotia stand; species limited to those covered by the donor taper",
    "NU": "documented representative Nunavut treeline stand; there is no reported commercial harvest in the facility chain",
}


# ---------------------------------------------------------------------------
# Strict helpers
# ---------------------------------------------------------------------------


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise SchemaError(f"{name} must be finite, got {value!r}")
    return number


def _non_negative(name: str, value: float) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise SchemaError(f"{name} must be non-negative, got {value!r}")
    return number


def _positive(name: str, value: float) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise SchemaError(f"{name} must be positive, got {value!r}")
    return number


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    value = numerator / denominator
    return value if math.isfinite(value) else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_status_lines(directory: Path) -> list[str]:
    if not (directory / ".git").exists() and not (directory / ".git").is_file():
        return []
    result = subprocess.run(
        ["git", "-C", str(directory), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def assert_no_unexpected_deletions() -> list[str]:
    """Fail closed when either repository shows an unexpected ``D`` status.

    The check covers the parent repository and the ``data`` submodule.  An
    untracked file is not a deletion.  A staged or unstaged ``D`` in either
    status column halts the run before any output is written.
    """
    deletions: list[str] = []
    for root in (REPO_ROOT, REPO_ROOT / "data"):
        for line in _git_status_lines(root):
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                deletions.append(f"{root.name or 'parent'}: {line}")
    if deletions:
        raise RuntimeError(
            "unexpected git deletion(s); refusing to run national bucking pilot v1: "
            + "; ".join(deletions)
        )
    return []


# ---------------------------------------------------------------------------
# Taper dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PilotTaper:
    """A taper adapter with explicit local/donor provenance."""

    taper_id: str
    jurisdiction: str
    species: str
    source_jurisdiction: str
    bec_zone: str
    taper_status: str
    proxy: bool
    evidence: str
    source: str
    model: Any

    def diameter_inside_bark_cm(
        self, dbh_cm: float, height_m: float, h_m: float
    ) -> float:
        if self.source_jurisdiction == "QC":
            return float(self.model.diameter_inside_bark_cm(dbh_cm, height_m, h_m))
        diameter = float(
            diameter_inside_bark(h_m, dbh_cm, height_m, self.model.parameters)
        )
        return diameter if math.isfinite(diameter) and diameter > 0.0 else 0.0

    def volume_between_m3(
        self,
        dbh_cm: float,
        height_m: float,
        start_m: float,
        end_m: float,
        *,
        intervals: int = 64,
    ) -> float:
        start = max(0.0, float(start_m))
        end = min(float(height_m), float(end_m))
        if end <= start:
            return 0.0
        if intervals < 2:
            raise SchemaError("intervals must be at least 2")
        if intervals % 2:
            intervals += 1
        step = (end - start) / intervals
        total = 0.0
        for index in range(intervals + 1):
            # Clip the last node to the stem end: start + n*step can exceed
            # ``height_m`` by one floating-point ulp.
            h = min(start + index * step, float(height_m))
            diameter = self.diameter_inside_bark_cm(dbh_cm, height_m, h)
            weight = 1.0 if index in (0, intervals) else (4.0 if index % 2 else 2.0)
            total += weight * diameter * diameter
        return math.pi / 4.0 * (step / 3.0) * total * 1e-4

    def total_volume_m3(self, dbh_cm: float, height_m: float) -> float:
        return self.volume_between_m3(
            dbh_cm,
            height_m,
            STUMP_HEIGHT_M,
            height_m,
            intervals=400,
        )


def build_taper(
    province: str,
    species: str,
    qc_registry: SchneiderTaperRegistry,
    kozak_parameters: Mapping[tuple[str, str], Any],
) -> PilotTaper:
    """Resolve the local or donor taper for one province/species."""
    province = province.upper()
    species = species.upper()
    if province == "QC":
        model = qc_registry.resolve("QC", species)
        return PilotTaper(
            taper_id=model.taper_id,
            jurisdiction=province,
            species=species,
            source_jurisdiction="QC",
            bec_zone="QC",
            taper_status="local_published",
            proxy=False,
            evidence=model.evidence,
            source=model.source,
            model=model,
        )
    if province == "BC":
        bec = BEC_ANCHOR.get(species, "SBS")
        parameters = params_for(kozak_parameters, species, bec)
        _code, proxy = KOZAK_SPECIES.get(species, (species, True))
        return PilotTaper(
            taper_id=f"BC-KOZAK2004-{species}-{bec}",
            jurisdiction=province,
            species=species,
            source_jurisdiction="BC",
            bec_zone=bec,
            taper_status="local_published",
            proxy=proxy,
            evidence="published_table",
            source=KOZAK_SOURCE,
            model=_KozakHolder(parameters),
        )
    donor = DONOR_POLICY[province]
    if donor == "QC":
        model = qc_registry.resolve("QC", species)
        return PilotTaper(
            taper_id=f"{province}-DONOR-SCHNEIDER2013-{species}",
            jurisdiction=province,
            species=species,
            source_jurisdiction="QC",
            bec_zone="QC",
            taper_status="donor_published",
            proxy=False,
            evidence=model.evidence,
            source=(
                "Cross-jurisdiction donor taper: Quebec Schneider (2013) "
                f"fixed-effect tree model. {model.source}"
            ),
            model=model,
        )
    bec = BEC_ANCHOR.get(species, "SBS")
    parameters = params_for(kozak_parameters, species, bec)
    _code, proxy = KOZAK_SPECIES.get(species, (species, True))
    return PilotTaper(
        taper_id=f"{province}-DONOR-KOZAK2004-{species}-{bec}",
        jurisdiction=province,
        species=species,
        source_jurisdiction="BC",
        bec_zone=bec,
        taper_status="donor_published",
        proxy=True,
        evidence="published_table",
        source=(
            "Cross-jurisdiction donor taper: BC Kozak (2004) Version 6; "
            f"BC BEC anchor {bec} is not a local {province} BEC zone. {KOZAK_SOURCE}"
        ),
        model=_KozakHolder(parameters),
    )


@dataclass(frozen=True, slots=True)
class _KozakHolder:
    parameters: Any


# ---------------------------------------------------------------------------
# Sort rules and bucking
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PilotSortRule:
    jurisdiction: str
    rule_id: str
    sort_code: str
    species_group: str
    product: str
    grade: str
    grade_status: str
    sort_basis: str
    min_top_cm: float
    target_length_m: float
    max_length_m: float
    priority: int
    price_cad_m3: float
    price_deck_id: str
    evidence: str
    authority: str
    notes: str

    def __post_init__(self) -> None:
        if self.evidence not in EVIDENCE_TIERS:
            raise SchemaError(f"sort rule evidence must be one of {EVIDENCE_TIERS}")
        if self.target_length_m <= 0.0 or self.min_top_cm <= 0.0:
            raise SchemaError("sort rule length and top diameter must be positive")
        if self.price_cad_m3 < 0.0:
            raise SchemaError("sort rule price must be non-negative")
        if self.grade != "not_assigned" or self.grade_status != "not_assigned":
            raise SchemaError("pilot sort rules must not assign an official grade")


def load_national_sort_rules(path: Path = SORT_RULES_PATH) -> tuple[PilotSortRule, ...]:
    import csv

    rules: list[PilotSortRule] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rules.append(
                PilotSortRule(
                    jurisdiction=str(row["jurisdiction"]).strip().upper(),
                    rule_id=str(row["rule_id"]).strip(),
                    sort_code=str(row["sort_code"]).strip().upper(),
                    species_group=str(row["species_group"]).strip().lower(),
                    product=str(row["product"]).strip().lower(),
                    grade=str(row["grade"]).strip(),
                    grade_status=str(row["grade_status"]).strip(),
                    sort_basis=str(row["sort_basis"]).strip(),
                    min_top_cm=float(row["min_top_cm"]),
                    target_length_m=float(row["target_length_m"]),
                    max_length_m=float(row["max_length_m"]),
                    priority=int(row["priority"]),
                    price_cad_m3=float(row["price_cad_m3"]),
                    price_deck_id=str(row["price_deck_id"]).strip(),
                    evidence=str(row["evidence"]).strip(),
                    authority=str(row["authority"]).strip(),
                    notes=str(row["notes"]).strip(),
                )
            )
    if not rules:
        raise SchemaError(f"no sort rules found in {path}")
    return tuple(sorted(rules, key=lambda rule: rule.priority))


def _rules_for_group(
    rules: Iterable[PilotSortRule], group: str
) -> tuple[PilotSortRule, ...]:
    selected = tuple(rule for rule in rules if rule.species_group == group)
    if not selected:
        raise SchemaError(f"no sort rules for species group {group}")
    return selected


def _best_rule(
    rules: Iterable[PilotSortRule], length_m: float, top_dib_cm: float
) -> PilotSortRule | None:
    eligible = [
        rule
        for rule in rules
        if math.isclose(rule.target_length_m, length_m, abs_tol=1e-9)
        and top_dib_cm + 1e-9 >= rule.min_top_cm
        and length_m <= rule.max_length_m + 1e-9
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda rule: (rule.price_cad_m3, -rule.priority))


@dataclass(frozen=True, slots=True)
class _Cut:
    start_m: float
    end_m: float
    rule: PilotSortRule
    volume_m3: float


def _solve(
    taper: PilotTaper,
    tree: PilotTree,
    rules: tuple[PilotSortRule, ...],
    lengths: tuple[float, ...],
    position_m: float,
    memo: dict[float, tuple[float, tuple[_Cut, ...]]],
    depth: int = 0,
) -> tuple[float, tuple[_Cut, ...]]:
    key = round(position_m, 6)
    if key in memo:
        return memo[key]
    if depth > 48:
        raise SchemaError("bucking recursion exceeded the maximum log count")
    best_value = 0.0
    best_cuts: tuple[_Cut, ...] = ()
    for length in lengths:
        top = position_m + length
        if top >= tree.height_m - 1e-6:
            continue
        top_dib = taper.diameter_inside_bark_cm(tree.dbh_cm, tree.height_m, top)
        rule = _best_rule(rules, length, top_dib)
        if rule is None:
            continue
        volume = taper.volume_between_m3(
            tree.dbh_cm, tree.height_m, position_m, top, intervals=64
        )
        if volume <= 1e-12:
            continue
        value = volume * (1.0 - tree.defect_pct / 100.0) * rule.price_cad_m3
        future_value, future_cuts = _solve(
            taper, tree, rules, lengths, top, memo, depth + 1
        )
        candidate = value + future_value
        if candidate > best_value + 1e-12:
            best_value = candidate
            best_cuts = (_Cut(position_m, top, rule, volume),) + future_cuts
    memo[key] = (best_value, best_cuts)
    return memo[key]


def buck_tree(
    tree: PilotTree,
    taper: PilotTaper,
    rules: tuple[PilotSortRule, ...],
) -> tuple[list[PilotBuckedLog], PilotResidueLedger]:
    if tree.status != "live":
        raise SchemaError(
            f"tree {tree.tree_id} is {tree.status!r}; only live trees are bucked"
        )
    if tree.taper_id != taper.taper_id:
        raise SchemaError(
            f"tree {tree.tree_id} taper_id {tree.taper_id!r} does not match "
            f"resolved taper {taper.taper_id!r}"
        )
    group = "deciduous" if tree.species in DECIDUOUS_CODES else "conifer"
    active_rules = _rules_for_group(rules, group)
    lengths = tuple(
        sorted({rule.target_length_m for rule in active_rules}, reverse=True)
    )
    total_volume = taper.total_volume_m3(tree.dbh_cm, tree.height_m)
    _, cuts = _solve(taper, tree, active_rules, lengths, STUMP_HEIGHT_M, {})

    logs: list[PilotBuckedLog] = []
    recovered_gross = 0.0
    recovered_net = 0.0
    defect_total = 0.0
    for index, cut in enumerate(cuts, start=1):
        start_dib = taper.diameter_inside_bark_cm(
            tree.dbh_cm, tree.height_m, cut.start_m
        )
        end_dib = taper.diameter_inside_bark_cm(tree.dbh_cm, tree.height_m, cut.end_m)
        # A published taper can show butt swell above the 0.3 m stump on small
        # stems.  The contract records the larger and smaller end diameters;
        # the integrated volume is unaffected by which end is lower.
        large_dib = max(start_dib, end_dib)
        small_dib = min(start_dib, end_dib)
        net_volume = cut.volume_m3 * (1.0 - tree.defect_pct / 100.0)
        defect_volume = cut.volume_m3 - net_volume
        value = net_volume * cut.rule.price_cad_m3
        logs.append(
            PilotBuckedLog(
                tree_id=tree.tree_id,
                jurisdiction=tree.jurisdiction,
                log_index=index,
                length_m=cut.end_m - cut.start_m,
                large_end_dib_cm=large_dib,
                small_end_dib_cm=small_dib,
                volume_m3=cut.volume_m3,
                net_volume_m3=net_volume,
                defect_deduction_m3=defect_volume,
                sort_code=cut.rule.sort_code,
                product=cut.rule.product,
                grade=cut.rule.grade,
                grade_status=cut.rule.grade_status,
                sort_basis=cut.rule.sort_basis,
                price_cad_m3=cut.rule.price_cad_m3,
                value_cad=value,
                evidence=weakest_evidence(
                    (taper.evidence, cut.rule.evidence, "assumption", tree.evidence)
                ),
                taper_evidence=taper.evidence,
                sort_evidence=cut.rule.evidence,
                value_evidence="assumption",
                tree_evidence=tree.evidence,
                taper_id=taper.taper_id,
                taper_source=taper.source,
                grade_rule_id=cut.rule.rule_id,
                price_deck_id=cut.rule.price_deck_id,
                provenance=tree.provenance,
            )
        )
        recovered_gross += cut.volume_m3
        recovered_net += net_volume
        defect_total += defect_volume

    residue_volume = total_volume - recovered_gross
    tolerance = max(1e-9, 1e-7 * max(total_volume, 1e-9))
    if residue_volume < -tolerance:
        raise SchemaError(
            f"tree {tree.tree_id}: recovered {recovered_gross:.9f} exceeds taper "
            f"total {total_volume:.9f}"
        )
    residue_volume = max(0.0, residue_volume)
    density = BASIC_DENSITY_ODT_M3.get(tree.species)
    if density is None:
        raise SchemaError(f"no basic density for species {tree.species}")
    residue_odt = residue_volume * density
    green_tonnes = residue_odt / (1.0 - MOISTURE_WET_BASIS)
    chip_green = green_tonnes * CHIP_YIELD_FRACTION
    hog_green = green_tonnes * HOG_YIELD_FRACTION
    residue = PilotResidueLedger(
        tree_id=tree.tree_id,
        jurisdiction=tree.jurisdiction,
        species=tree.species,
        residue_volume_m3=residue_volume,
        basic_density_odt_m3=density,
        residue_odt=residue_odt,
        moisture_wet_basis=MOISTURE_WET_BASIS,
        green_tonnes=green_tonnes,
        chip_yield_fraction=CHIP_YIELD_FRACTION,
        chip_green_tonnes=chip_green,
        hog_yield_fraction=HOG_YIELD_FRACTION,
        hog_green_tonnes=hog_green,
        recovered_log_m3=recovered_gross,
        taper_total_m3=total_volume,
        balance_error_m3=abs(recovered_gross + residue_volume - total_volume),
        evidence=weakest_evidence((taper.evidence, "assumption")),
        taper_evidence=taper.evidence,
        density_evidence="assumption",
        moisture_evidence="assumption",
        chip_evidence="assumption",
        provenance=tree.provenance,
        notes=(
            f"residue = taper total {total_volume:.6f} m3 - recovered gross logs "
            f"{recovered_gross:.6f} m3; includes the stump section above the "
            f"{STUMP_HEIGHT_M:.2f} m taper base, top, and unmerchantable sections. "
            f"Volume below {STUMP_HEIGHT_M:.2f} m is outside the taper total. "
            f"Defect deduction in recovered logs "
            f"{defect_total:.6f} m3. Taper {taper.taper_id} "
            f"({taper.taper_status}, {taper.evidence}); density "
            f"{density:.2f} ODT/m3, moisture {MOISTURE_WET_BASIS:.2f} wet basis, "
            f"chip yield {CHIP_YIELD_FRACTION:.2f}, hog yield "
            f"{HOG_YIELD_FRACTION:.2f} are pilot assumptions."
        ),
    )
    return logs, residue


# ---------------------------------------------------------------------------
# Representative tree lists from open NFI coverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvinceComposition:
    province: str
    shares: tuple[tuple[str, float], ...]
    nfi_by_taper: dict[str, str]
    basis: str
    source: str


def _load_nfi_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not NFI_COVERAGE_PATH.exists():
        raise FileNotFoundError(f"NFI coverage table not found: {NFI_COVERAGE_PATH}")
    if not NFI_HEIGHT_PATH.exists():
        raise FileNotFoundError(f"NFI height model table not found: {NFI_HEIGHT_PATH}")
    coverage = pd.read_csv(
        NFI_COVERAGE_PATH,
        usecols=[
            "level",
            "species",
            "species_group",
            "province_primary",
            "provinces",
            "n_trees",
            "dbh_p05_cm",
            "dbh_p50_cm",
            "dbh_p95_cm",
        ],
        low_memory=False,
    )
    heights = pd.read_csv(
        NFI_HEIGHT_PATH,
        usecols=[
            "level",
            "species",
            "coverage_class",
            "a",
            "b",
            "rmse_m",
            "n_height_obs",
        ],
        low_memory=False,
    )
    national = coverage[coverage["level"] == "national"].drop_duplicates("species")
    ecodistrict = coverage[
        (coverage["level"] == "ecodistrict") & (coverage["n_trees"].fillna(0) > 0)
    ].copy()
    national_heights = heights[heights["level"] == "national"].drop_duplicates(
        "species"
    )
    return national, ecodistrict, national_heights


def _province_composition(
    province: str,
    ecodistrict: pd.DataFrame,
) -> ProvinceComposition:
    source_jurisdiction = (
        "QC" if province == "QC" or DONOR_POLICY.get(province) == "QC" else "BC"
    )
    mapping = NFI_TO_QC if source_jurisdiction == "QC" else NFI_TO_BC
    if province in ASSUMED_COMPOSITION:
        raw = ASSUMED_COMPOSITION[province]
        basis = "documented_assumption"
        source = ASSUMPTION_TEXT[province]
    else:
        tokens = (
            ecodistrict["provinces"]
            .fillna("")
            .astype(str)
            .apply(
                lambda text: {
                    part.strip().upper() for part in text.split(";") if part.strip()
                }
            )
        )
        rows = ecodistrict[tokens.apply(lambda values: province in values)].copy()
        if rows.empty:
            raise SchemaError(
                f"no NFI ecodistrict coverage rows for {province}; "
                "add an explicit documented assumption"
            )
        by_species = (
            rows.groupby("species")["n_trees"].sum().sort_values(ascending=False)
        )
        raw = tuple(
            (str(species), float(count)) for species, count in by_species.items()
        )
        basis = "nfi_coverage_composition"
        source = (
            "NFI coverage ecodistrict rows with province token match; "
            "n_trees-weighted species shares; unsupported species excluded"
        )
    by_taper: dict[str, float] = {}
    nfi_by_taper: dict[str, tuple[str, float]] = {}
    for nfi_species, share in raw:
        taper_species = mapping.get(str(nfi_species).upper())
        if taper_species is None:
            continue
        by_taper[taper_species] = by_taper.get(taper_species, 0.0) + float(share)
        previous = nfi_by_taper.get(taper_species)
        if previous is None or float(share) > previous[1]:
            nfi_by_taper[taper_species] = (str(nfi_species).upper(), float(share))
    if not by_taper:
        raise SchemaError(f"no taper-supported species for {province}")
    total = sum(by_taper.values())
    shares = tuple(
        sorted(
            ((species, share / total) for species, share in by_taper.items()),
            key=lambda item: (-item[1], item[0]),
        )
    )
    return ProvinceComposition(
        province=province,
        shares=shares,
        nfi_by_taper={species: value[0] for species, value in nfi_by_taper.items()},
        basis=basis,
        source=source,
    )


def _allocate_counts(shares: Sequence[tuple[str, float]], total: int) -> dict[str, int]:
    selected: list[tuple[str, float]] = []
    cumulative = 0.0
    for species, share in shares:
        if len(selected) >= MAX_SPECIES_PER_PROVINCE:
            break
        selected.append((species, share))
        cumulative += share
        if cumulative >= TARGET_SPECIES_COVERAGE and len(selected) >= 3:
            break
    if not selected:
        raise SchemaError("no species selected for allocation")
    selected_total = sum(share for _, share in selected)
    if selected_total <= 0.0:
        raise SchemaError("selected species shares sum to zero")
    if len(selected) > total:
        selected = selected[:total]
        selected_total = sum(share for _, share in selected)
    target = [(species, share / selected_total * total) for species, share in selected]
    counts = {species: 1 for species, _ in target}
    remaining = total - len(counts)
    if remaining < 0:
        raise SchemaError("more species than trees")
    remainders: list[tuple[float, str]] = []
    for species, quota in target:
        excess = max(0.0, quota - 1.0)
        whole = math.floor(excess)
        add = min(whole, remaining)
        counts[species] += add
        remaining -= add
        remainders.append((excess - whole, species))
    remainders.sort(key=lambda item: (-item[0], item[1]))
    while remaining > 0:
        progressed = False
        for _fraction, species in remainders:
            if remaining <= 0:
                break
            counts[species] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            raise SchemaError("could not allocate tree counts")
    if sum(counts.values()) != total:
        raise SchemaError(
            f"tree allocation sums to {sum(counts.values())}, expected {total}"
        )
    return counts


def _dbh_quantiles(
    nfi_species: str, national: pd.DataFrame
) -> tuple[float, float, float]:
    row = national[national["species"] == nfi_species]
    if row.empty:
        return 8.0, 18.0, 32.0
    record = row.iloc[0]
    values = [
        float(record["dbh_p05_cm"]),
        float(record["dbh_p50_cm"]),
        float(record["dbh_p95_cm"]),
    ]
    if not all(math.isfinite(value) and value > 0.0 for value in values):
        return 8.0, 18.0, 32.0
    return values[0], values[1], values[2]


def _height_coefficients(
    nfi_species: str, heights: pd.DataFrame
) -> tuple[float, float, float, float]:
    row = heights[heights["species"] == nfi_species]
    if row.empty:
        return math.log(1.3), 0.80, 0.0, 0.0
    record = row.iloc[0]
    a = float(record["a"])
    b = float(record["b"])
    rmse = float(record["rmse_m"]) if pd.notna(record["rmse_m"]) else 0.0
    n_obs = float(record["n_height_obs"]) if pd.notna(record["n_height_obs"]) else 0.0
    if not math.isfinite(a) or not math.isfinite(b):
        return math.log(1.3), 0.80, rmse, n_obs
    return a, b, rmse, n_obs


def _dbh_values(
    p05: float, p50: float, p95: float, count: int, max_dbh: float
) -> list[float]:
    p50 = min(max(p50, 8.0), max_dbh)
    high = min(max(p95, p50 * 1.6), max_dbh)
    low = min(max(7.5, min(p05, p50 * 0.8)), p50)
    if count == 1:
        positions = [0.5]
    else:
        positions = [0.05 + 0.90 * index / (count - 1) for index in range(count)]
    values: list[float] = []
    for position in positions:
        if position <= 0.5:
            dbh = low + (p50 - low) * (position / 0.5)
        else:
            dbh = p50 + (high - p50) * ((position - 0.5) / 0.5)
        values.append(min(max(dbh, 7.5), max_dbh))
    return values


def _height_value(dbh_cm: float, a: float, b: float, max_height: float) -> float:
    height = 1.3 + math.exp(a) * (dbh_cm**b)
    return min(max(height, 6.0), max_height)


def build_representative_tree_lists(
    qc_registry: SchneiderTaperRegistry,
    kozak_parameters: Mapping[tuple[str, str], Any],
) -> tuple[
    tuple[PilotTree, ...],
    dict[str, PilotTaper],
    dict[str, dict[str, Any]],
    dict[str, ProvinceComposition],
]:
    """Build ten deterministic representative trees per province.

    Species shares come from NFI ecodistrict coverage where available and from
    explicit documented assumptions otherwise.  DBH draws use NFI national
    p05/p50/p95 quantiles with an 8 cm merchantable lower bound; height uses
    the NFI national log-linear height-diameter fit.  The result is
    representative synthetic geometry, not an inventory tree list.
    """
    national, ecodistrict, heights = _load_nfi_frames()
    trees: list[PilotTree] = []
    tapers: dict[str, PilotTaper] = {}
    extras: dict[str, dict[str, Any]] = {}
    compositions: dict[str, ProvinceComposition] = {}
    for province in PROVINCES:
        composition = _province_composition(province, ecodistrict)
        compositions[province] = composition
        counts = _allocate_counts(composition.shares, N_TREES_PER_PROVINCE)
        province_index = 0
        for species, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        ):
            taper = build_taper(province, species, qc_registry, kozak_parameters)
            nfi_species = composition.nfi_by_taper.get(species, species)
            p05, p50, p95 = _dbh_quantiles(nfi_species, national)
            a, b, height_rmse, height_n = _height_coefficients(nfi_species, heights)
            max_dbh = 95.0 if taper.source_jurisdiction == "QC" else 150.0
            max_height = 39.5 if taper.source_jurisdiction == "QC" else 60.0
            dbhs = _dbh_values(p05, p50, p95, count, max_dbh)
            for dbh in dbhs:
                province_index += 1
                height = _height_value(dbh, a, b, max_height)
                defect = (province_index - 1) % 6 * 2.5
                tree_id = f"{province}-V1-T{province_index:03d}"
                provenance = (
                    "representative_synthetic_v1: "
                    f"{composition.basis}; species={species} from NFI {nfi_species}; "
                    f"DBH from NFI national p05/p50/p95 with 8 cm merchantable lower "
                    f"bound; height from NFI national H=1.3+exp(a)*DBH^b "
                    f"(rmse={height_rmse:.1f} m, n={int(height_n)}); deterministic "
                    "quantile draws; not inventory; no PSP link"
                )
                tree = PilotTree(
                    tree_id=tree_id,
                    site_id=f"{province}-V1-REP",
                    jurisdiction=province,
                    species=species,
                    species_group=(
                        "deciduous" if species in DECIDUOUS_CODES else "conifer"
                    ),
                    dbh_cm=dbh,
                    height_m=height,
                    defect_pct=float(defect),
                    expansion_factor=1.0,
                    status="live",
                    provenance=provenance,
                    evidence="assumption",
                    taper_id=taper.taper_id,
                )
                trees.append(tree)
                tapers[tree_id] = taper
                extras[tree_id] = {
                    "taper_status": taper.taper_status,
                    "taper_jurisdiction": taper.source_jurisdiction,
                    "taper_bec_zone": taper.bec_zone,
                    "taper_proxy": taper.proxy,
                    "species_nfi": nfi_species,
                    "tree_list_basis": composition.basis,
                    "tree_list_source": composition.source,
                    "dbh_basis": "NFI national p05/p50/p95, merchantable lower bound 8 cm",
                    "height_basis": "NFI national H=1.3+exp(a)*DBH^b",
                    "model_estimate": True,
                    "synthetic": True,
                    "observed": False,
                    "policy_grade": False,
                    "national_claim": False,
                }
    if len(trees) != len(PROVINCES) * N_TREES_PER_PROVINCE:
        raise SchemaError(
            f"expected {len(PROVINCES) * N_TREES_PER_PROVINCE} trees, got {len(trees)}"
        )
    return tuple(trees), tapers, extras, compositions


# ---------------------------------------------------------------------------
# Province and facility summaries
# ---------------------------------------------------------------------------


def _province_summary(
    trees: Sequence[PilotTree],
    logs: Sequence[PilotBuckedLog],
    residues: Sequence[PilotResidueLedger],
    tapers: Mapping[str, PilotTaper],
    extras: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    logs_by_province: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by_province.setdefault(log.jurisdiction, []).append(log)
    residues_by_province: dict[str, list[PilotResidueLedger]] = {}
    for residue in residues:
        residues_by_province.setdefault(residue.jurisdiction, []).append(residue)
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        province_trees = [tree for tree in trees if tree.jurisdiction == province]
        province_logs = logs_by_province.get(province, [])
        province_residues = residues_by_province.get(province, [])
        products: dict[str, float] = {}
        for log in province_logs:
            products[log.product] = products.get(log.product, 0.0) + log.volume_m3
        taper_total = sum(row.taper_total_m3 for row in province_residues)
        recovered = sum(row.recovered_log_m3 for row in province_residues)
        residue_m3 = sum(row.residue_volume_m3 for row in province_residues)
        residue_odt = sum(row.residue_odt for row in province_residues)
        local_count = sum(
            1
            for tree in province_trees
            if extras[tree.tree_id]["taper_status"] == "local_published"
        )
        donor_count = len(province_trees) - local_count
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "n_trees": len(province_trees),
                "n_logs": len(province_logs),
                "n_species": len({tree.species for tree in province_trees}),
                "taper_total_m3": taper_total,
                "recovered_gross_m3": recovered,
                "recovered_net_m3": sum(log.net_volume_m3 for log in province_logs),
                "defect_m3": sum(log.defect_deduction_m3 for log in province_logs),
                "residue_m3": residue_m3,
                "residue_odt": residue_odt,
                "green_tonnes": sum(row.green_tonnes for row in province_residues),
                "chip_green_tonnes": sum(
                    row.chip_green_tonnes for row in province_residues
                ),
                "hog_green_tonnes": sum(
                    row.hog_green_tonnes for row in province_residues
                ),
                "sawlog_m3": products.get("sawlog", 0.0),
                "pulpwood_m3": products.get("pulpwood", 0.0),
                "other_m3": products.get("other", 0.0),
                "value_cad": sum(log.value_cad for log in province_logs),
                "sawlog_share_of_recovered": _safe_div(
                    products.get("sawlog", 0.0), recovered
                ),
                "pulpwood_share_of_recovered": _safe_div(
                    products.get("pulpwood", 0.0), recovered
                ),
                "other_share_of_recovered": _safe_div(
                    products.get("other", 0.0), recovered
                ),
                "residue_m3_per_recovered_m3": _safe_div(residue_m3, recovered),
                "residue_odt_per_recovered_m3": _safe_div(residue_odt, recovered),
                "product_residue_ratio_m3_per_odt": _safe_div(recovered, residue_odt),
                "local_taper_trees": local_count,
                "donor_taper_trees": donor_count,
                "volume_balance_m3": abs(recovered + residue_m3 - taper_total),
                "max_tree_balance_error_m3": max(
                    (row.balance_error_m3 for row in province_residues), default=0.0
                ),
                "tree_list_basis": min(
                    {extras[tree.tree_id]["tree_list_basis"] for tree in province_trees}
                ),
                "taper_status": (
                    "local_published" if donor_count == 0 else "donor_published"
                ),
                "model_estimate": True,
                "synthetic": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    return rows


def _coefficient_comparison(
    province_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not FACILITY_CHAIN_PATH.exists():
        raise FileNotFoundError(
            f"facility product chain not found: {FACILITY_CHAIN_PATH}"
        )
    facility = pd.read_csv(FACILITY_CHAIN_PATH)
    facility_by_province = {
        str(row["province"]).strip().upper(): row for _, row in facility.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for province_row in province_rows:
        province = str(province_row["province"])
        record = facility_by_province.get(province)
        if record is None:
            raise SchemaError(f"facility product chain has no row for {province}")
        harvest = float(record["harvest_m3"])
        facility_sawlog_share = _safe_div(float(record["sawlog_feedstock_m3"]), harvest)
        facility_pulp_share = _safe_div(float(record["pulp_feedstock_m3"]), harvest)
        facility_solid_share = _safe_div(
            float(record["sawlog_feedstock_m3"]) + float(record["panel_feedstock_m3"]),
            harvest,
        )
        facility_harvest_residue = _safe_div(
            float(record["harvest_residue_gross_odt"]), harvest
        )
        facility_process_residue = _safe_div(
            float(record["process_residue_odt"]), float(record["facility_feedstock_m3"])
        )
        facility_product_residue = _safe_div(
            harvest, float(record["harvest_residue_gross_odt"])
        )
        model_sawlog = float(province_row["sawlog_share_of_recovered"])
        model_pulp = float(province_row["pulpwood_share_of_recovered"])
        model_other = float(province_row["other_share_of_recovered"])
        model_solid = model_sawlog + model_other
        model_residue = float(province_row["residue_odt_per_recovered_m3"])
        model_product_residue = float(province_row["product_residue_ratio_m3_per_odt"])
        is_comparable = (
            harvest > 0.0 and float(province_row["recovered_gross_m3"]) > 0.0
        )
        sawlog_ratio = _safe_div(model_sawlog, facility_sawlog_share)
        solid_ratio = _safe_div(model_solid, facility_solid_share)
        residue_ratio = _safe_div(model_residue, facility_harvest_residue)
        if not is_comparable:
            comparison_status = "not_comparable_zero_harvest"
        elif 0.8 <= solid_ratio <= 1.2 and 0.8 <= residue_ratio <= 1.2:
            comparison_status = "within_20pct"
        else:
            comparison_status = "outside_20pct"
        donor = province_row["taper_status"] == "donor_published"
        dominant = "synthetic_tree_list;dimension_sort_assumption;" + (
            "donor_taper" if donor else "local_taper"
        )
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "model_n_trees": int(province_row["n_trees"]),
                "model_recovered_m3": float(province_row["recovered_gross_m3"]),
                "model_sawlog_share": model_sawlog,
                "model_pulpwood_share": model_pulp,
                "model_other_share": model_other,
                "model_solid_share": model_solid,
                "model_residue_m3_per_recovered_m3": float(
                    province_row["residue_m3_per_recovered_m3"]
                ),
                "model_residue_odt_per_recovered_m3": model_residue,
                "model_product_residue_ratio_m3_per_odt": model_product_residue,
                "facility_harvest_m3": harvest,
                "facility_sawlog_feedstock_share": facility_sawlog_share,
                "facility_pulp_feedstock_share": facility_pulp_share,
                "facility_solid_feedstock_share": facility_solid_share,
                "facility_harvest_residue_odt_per_m3": facility_harvest_residue,
                "facility_process_residue_odt_per_feedstock_m3": facility_process_residue,
                "facility_product_residue_ratio_m3_per_odt": facility_product_residue,
                "sawlog_share_model_over_facility": sawlog_ratio,
                "solid_share_model_over_facility": solid_ratio,
                "residue_odt_model_over_facility": residue_ratio,
                "product_residue_ratio_model_over_facility": _safe_div(
                    model_product_residue, facility_product_residue
                ),
                "comparison_status": comparison_status,
                "facility_coefficient_tier": str(record.get("coefficient_tier", "")),
                "taper_status": province_row["taper_status"],
                "tree_list_basis": province_row["tree_list_basis"],
                "dominant_factor": dominant,
                "donor_or_assumption_dominates": True,
                "model_estimate": True,
                "synthetic": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
                "notes": (
                    "Model volumes are unweighted representative-tree logs. "
                    "Facility feedstock shares are from facility_product_chain_v1. "
                    "Harvest residue and facility process residue are different streams; "
                    "the process-residue coefficient is shown for context only."
                ),
            }
        )
    if len(rows) != len(PROVINCES):
        raise SchemaError(
            f"expected {len(PROVINCES)} coefficient rows, got {len(rows)}"
        )
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> int:
    import csv

    materialized = list(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(row)
    return len(materialized)


def _schema_markdown() -> str:
    return """# National bucking pilot v1 schema

Every record is a pilot record.  `model_estimate=true`, `synthetic=true`,
`observed=false`, `policy_grade=false`, `national_claim=false`.

## Evidence tiers

`measured_taper`, `published_table`, `assumption`.  A row's `evidence` is the
weakest component tier.  `taper_status` separately says whether the published
taper is local (`local_published`) or a cross-jurisdiction donor
(`donor_published`).  `taper_proxy=true` means the species was mapped to a
published aggregate/proxy species in the donor table.

## Tables

| Table | Grain | Key fields |
|---|---|---|
| `tree_list.csv` | one representative tree | `tree_id`, `jurisdiction`, `species`, `dbh_cm`, `height_m`, `taper_id`, `taper_status`, `species_nfi`, `tree_list_basis` |
| `bucked_logs.csv` | one recovered log | `tree_id`, `log_index`, `length_m`, `large_end_dib_cm`, `small_end_dib_cm`, `volume_m3`, `net_volume_m3`, `product`, `grade`, `sort_basis`, `taper_evidence`, `taper_status` |
| `residue_ledger.csv` | one stem | `residue_volume_m3`, `residue_odt`, `green_tonnes`, `chip_green_tonnes`, `hog_green_tonnes`, `recovered_log_m3`, `taper_total_m3`, `balance_error_m3` |
| `reconciliation.csv` | one stem | total/recovered/residue/defect/net/log count/product volumes |
| `province_summary.csv` | one province | volume, residue, product shares, taper local/donor counts |
| `coefficient_comparison.csv` | one province | model ratios versus facility-chain coefficients |

`grade=not_assigned` and `grade_status=not_assigned` in every log row.  The sort
set is `dimension_only`; it is not an official federal or provincial grade.
"""


def _readme() -> str:
    return """# National bucking pilot v1 — 13 jurisdictions

**Scope: representative synthetic pilot, not inventory and not a national
supply estimate.**

This directory exercises the national bucking contracts across all 13
jurisdictions using ten representative trees per province.  Species shares use
the open NFI coverage ecodistrict table where available; Alberta, Ontario, Nova
Scotia and Nunavut use explicit documented composition assumptions because the
open coverage table has no provincial rows for them.  DBH draws use NFI
national species p05/p50/p95 quantiles with an 8 cm merchantable lower bound;
height uses the NFI national log-linear height-diameter fit.  The trees are
deterministic synthetic draws.

Taper dispatch:

- Quebec: Schneider (2013) local published fixed-effect taper.
- British Columbia: Kozak (2004) local published Version 6 taper via `nemora`.
- Other provinces: documented donor taper (BC Kozak for western/boreal
  provinces, Quebec Schneider for eastern/Acadian provinces).  Every donor row
  says `taper_status=donor_published`; the BEC anchor is a BC anchor, not a
  local BEC zone.

Products are dimension-only pilot sorts.  `grade=not_assigned`; no official
grade, policy grade, or market price is claimed.  Residue is defined as taper
total minus recovered gross logs, so the stump section above the 0.3 m taper
base, top and unmerchantable material are explicit; volume below the taper base
is outside the ledger.

## Files

| File | Content |
|---|---|
| `tree_list.csv` | Combined representative tree list. |
| `tree_lists/*.csv` | Per-province tree lists. |
| `bucked_logs.csv` | One row per recovered log. |
| `residue_ledger.csv` | One row per stem. |
| `reconciliation.csv` | Per-tree volume reconciliation. |
| `province_summary.csv` | Logs, residue and product shares by province. |
| `coefficient_comparison.csv` | Model ratios versus facility-chain coefficients. |
| `summary.json` | Machine-readable totals, labels and checks. |
| `report.md` | Human-readable pilot report. |
| `manifest.json` | Code, inputs, outputs, checksums and scope. |
| `taper_dispatch.csv` | Taper actually resolved for every tree. |
| `sort_rules.csv` | National dimension-only pilot sort set. |
| `schema.md` | Field and evidence-tier contract. |

## Reproduce

```bash
.venv/bin/python -m workflows.national.bucking.national_bucking_pilot_v1 \
  --out-dir data/derived/national/bucking/national_bucking_pilot_v1
```
"""


def _report_markdown(
    summary: Mapping[str, Any],
    province_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# National bucking pilot v1 — 13 jurisdictions\n")
    lines.append(
        "**Scope: representative synthetic pilot.**  Not inventory, not a "
        "national supply estimate, not an official grade.  Every row is "
        "`model_estimate=true`, `synthetic=true`, `observed=false`, "
        "`policy_grade=false`, `national_claim=false`.\n"
    )
    lines.append("## Method\n")
    lines.append(
        "- Ten representative trees per province.  Species shares use NFI "
        "ecodistrict coverage where available; AB/ON/NS/NU use documented "
        "composition assumptions.  DBH uses NFI national p05/p50/p95 with an "
        "8 cm merchantable lower bound; height uses the NFI national "
        "H = 1.3 + exp(a) * DBH^b fit.  Trees are deterministic synthetic "
        "draws, not inventory.\n"
        "- Taper dispatch is explicit: QC uses Schneider (2013), BC uses "
        "Kozak (2004), and the other provinces use a labelled donor taper.  "
        "Donor BEC anchors are BC anchors, not local BEC zones.\n"
        "- Products use one national dimension-only pilot sort set.  "
        "`grade=not_assigned`; defect, sweep, knot and rot are not represented.\n"
        "- Residue is taper total minus recovered gross log volume per stem; "
        "density, moisture, chip and hog yields are labelled assumptions.\n"
    )
    lines.append("## Taper coverage\n")
    lines.append("| Province | Trees | Local taper | Donor taper | Taper status |\n")
    lines.append("|---|---:|---:|---:|---|\n")
    for row in province_rows:
        lines.append(
            f"| {row['province']} | {row['n_trees']} | {row['local_taper_trees']} | "
            f"{row['donor_taper_trees']} | {row['taper_status']} |\n"
        )
    lines.append("\n## Logs and residue by province\n")
    lines.append(
        "| Province | Trees | Logs | Recovered m3 | Sawlog m3 | Pulpwood m3 | "
        "Other m3 | Residue m3 | Residue odt | Balance error m3 |\n"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for row in province_rows:
        lines.append(
            f"| {row['province']} | {row['n_trees']} | {row['n_logs']} | "
            f"{row['recovered_gross_m3']:.4f} | {row['sawlog_m3']:.4f} | "
            f"{row['pulpwood_m3']:.4f} | {row['other_m3']:.4f} | "
            f"{row['residue_m3']:.4f} | {row['residue_odt']:.4f} | "
            f"{row['max_tree_balance_error_m3']:.3g} |\n"
        )
    lines.append("\n## Facility coefficient comparison\n")
    lines.append(
        "| Province | Model solid share | Facility solid share | "
        "Model residue odt/m3 | Facility harvest residue odt/m3 | Status | "
        "Dominant factor |\n"
    )
    lines.append("|---|---:|---:|---:|---:|---|---|\n")
    for row in comparison_rows:
        lines.append(
            f"| {row['province']} | {row['model_solid_share']:.3f} | "
            f"{row['facility_solid_feedstock_share']:.3f} | "
            f"{row['model_residue_odt_per_recovered_m3']:.4f} | "
            f"{row['facility_harvest_residue_odt_per_m3']:.4f} | "
            f"{row['comparison_status']} | {row['dominant_factor']} |\n"
        )
    lines.append("\n## Checks\n")
    checks = summary.get("checks", [])
    for check in checks:
        lines.append(
            f"- `{check['check_id']}`: **{check['status']}** — {check['detail']}\n"
        )
    lines.append("\n## Limitations\n")
    for limitation in summary.get("limitations", []):
        lines.append(f"- {limitation}\n")
    lines.append(
        "\nNo UI, public OSeCAN surface, policy grade, AAC, or national supply "
        "claim is made.\n"
    )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_pilot(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Build the 13-jurisdiction pilot and write the output directory."""
    deletions = assert_no_unexpected_deletions()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    qc_registry = load_taper_registry(QC_TAPER_PATH)
    kozak_path = default_kozak_parameter_path()
    kozak_parameters = load_kozak_parameters(kozak_path)
    rules = load_national_sort_rules(SORT_RULES_PATH)
    trees, tapers, extras, _compositions = build_representative_tree_lists(
        qc_registry, kozak_parameters
    )

    logs: list[PilotBuckedLog] = []
    residues: list[PilotResidueLedger] = []
    for tree in trees:
        taper = tapers[tree.tree_id]
        tree_logs, residue = buck_tree(tree, taper, rules)
        logs.extend(tree_logs)
        residues.append(residue)
    if not logs:
        raise SchemaError("pilot produced no logs")

    logs_by_tree: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by_tree.setdefault(log.tree_id, []).append(log)
    tree_by_id = {tree.tree_id: tree for tree in trees}

    tree_rows: list[dict[str, Any]] = []
    for tree in trees:
        row = tree.to_dict()
        row.update(extras[tree.tree_id])
        tree_rows.append(row)

    log_rows: list[dict[str, Any]] = []
    for log in logs:
        row = log.to_dict()
        extra = extras[log.tree_id]
        row.update(
            {
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "model_estimate": True,
                "synthetic": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        log_rows.append(row)

    residue_rows: list[dict[str, Any]] = []
    for residue in residues:
        row = residue.to_dict()
        extra = extras[residue.tree_id]
        row.update(
            {
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "model_estimate": True,
                "synthetic": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        residue_rows.append(row)

    reconciliation_rows: list[dict[str, Any]] = []
    for residue in residues:
        tree = tree_by_id[residue.tree_id]
        tree_logs = logs_by_tree.get(residue.tree_id, [])
        products: dict[str, float] = {}
        for log in tree_logs:
            products[log.product] = products.get(log.product, 0.0) + log.volume_m3
        reconciliation_rows.append(
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "defect_pct": tree.defect_pct,
                "taper_total_m3": residue.taper_total_m3,
                "recovered_gross_m3": residue.recovered_log_m3,
                "residue_m3": residue.residue_volume_m3,
                "defect_m3": sum(log.defect_deduction_m3 for log in tree_logs),
                "recovered_net_m3": sum(log.net_volume_m3 for log in tree_logs),
                "n_logs": len(tree_logs),
                "sawlog_m3": products.get("sawlog", 0.0),
                "pulpwood_m3": products.get("pulpwood", 0.0),
                "other_m3": products.get("other", 0.0),
                "value_cad": sum(log.value_cad for log in tree_logs),
                "balance_error_m3": residue.balance_error_m3,
                "evidence": weakest_evidence(
                    [residue.evidence, *(log.evidence for log in tree_logs)]
                ),
                "taper_evidence": residue.taper_evidence,
                "taper_id": tree.taper_id,
                "taper_status": extras[tree.tree_id]["taper_status"],
                "taper_jurisdiction": extras[tree.tree_id]["taper_jurisdiction"],
                "taper_proxy": extras[tree.tree_id]["taper_proxy"],
                "species_nfi": extras[tree.tree_id]["species_nfi"],
                "tree_list_basis": extras[tree.tree_id]["tree_list_basis"],
                "model_estimate": True,
                "synthetic": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )

    province_rows = _province_summary(trees, logs, residues, tapers, extras)
    comparison_rows = _coefficient_comparison(province_rows)

    totals = {
        "n_trees": len(trees),
        "n_logs": len(logs),
        "taper_total_m3": sum(row["taper_total_m3"] for row in reconciliation_rows),
        "recovered_gross_m3": sum(
            row["recovered_gross_m3"] for row in reconciliation_rows
        ),
        "recovered_net_m3": sum(row["recovered_net_m3"] for row in reconciliation_rows),
        "defect_m3": sum(row["defect_m3"] for row in reconciliation_rows),
        "residue_m3": sum(row["residue_m3"] for row in reconciliation_rows),
        "residue_odt": sum(row.residue_odt for row in residues),
        "green_tonnes": sum(row.green_tonnes for row in residues),
        "chip_green_tonnes": sum(row.chip_green_tonnes for row in residues),
        "hog_green_tonnes": sum(row.hog_green_tonnes for row in residues),
        "value_cad": sum(row["value_cad"] for row in reconciliation_rows),
        "sawlog_m3": sum(row["sawlog_m3"] for row in reconciliation_rows),
        "pulpwood_m3": sum(row["pulpwood_m3"] for row in reconciliation_rows),
        "other_m3": sum(row["other_m3"] for row in reconciliation_rows),
        "max_balance_error_m3": max(
            row["balance_error_m3"] for row in reconciliation_rows
        ),
    }
    totals["balance_error_m3"] = abs(
        totals["recovered_gross_m3"] + totals["residue_m3"] - totals["taper_total_m3"]
    )
    totals["net_plus_defect_error_m3"] = abs(
        totals["recovered_net_m3"] + totals["defect_m3"] - totals["recovered_gross_m3"]
    )
    if totals["balance_error_m3"] > 1e-6:
        raise SchemaError(
            f"pilot volume balance failed: {totals['balance_error_m3']:.12g} m3"
        )
    if totals["net_plus_defect_error_m3"] > 1e-6:
        raise SchemaError(
            f"pilot net/defect balance failed: {totals['net_plus_defect_error_m3']:.12g} m3"
        )

    tree_columns = list(tree_rows[0])
    log_columns = list(log_rows[0])
    residue_columns = list(residue_rows[0])
    reconciliation_columns = list(reconciliation_rows[0])
    province_columns = list(province_rows[0])
    comparison_columns = list(comparison_rows[0])

    _write_csv(output / "tree_list.csv", tree_rows, tree_columns)
    (output / "tree_lists").mkdir(exist_ok=True)
    for province in PROVINCES:
        rows = [row for row in tree_rows if row["jurisdiction"] == province]
        _write_csv(output / "tree_lists" / f"{province}.csv", rows, tree_columns)
    _write_csv(output / "bucked_logs.csv", log_rows, log_columns)
    _write_csv(output / "residue_ledger.csv", residue_rows, residue_columns)
    _write_csv(
        output / "reconciliation.csv", reconciliation_rows, reconciliation_columns
    )
    _write_csv(output / "province_summary.csv", province_rows, province_columns)
    _write_csv(
        output / "coefficient_comparison.csv", comparison_rows, comparison_columns
    )
    _write_csv(
        output / "taper_dispatch.csv",
        [
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "species_nfi": extras[tree.tree_id]["species_nfi"],
                "taper_id": tapers[tree.tree_id].taper_id,
                "source_jurisdiction": tapers[tree.tree_id].source_jurisdiction,
                "bec_zone_or_anchor": tapers[tree.tree_id].bec_zone,
                "taper_status": tapers[tree.tree_id].taper_status,
                "taper_proxy": tapers[tree.tree_id].proxy,
                "taper_evidence": tapers[tree.tree_id].evidence,
                "taper_source": tapers[tree.tree_id].source,
            }
            for tree in trees
        ],
        [
            "tree_id",
            "jurisdiction",
            "species",
            "species_nfi",
            "taper_id",
            "source_jurisdiction",
            "bec_zone_or_anchor",
            "taper_status",
            "taper_proxy",
            "taper_evidence",
            "taper_source",
        ],
    )
    _write_csv(
        output / "sort_rules.csv",
        [asdict(rule) for rule in rules],
        list(asdict(rules[0])),
    )
    (output / "schema.md").write_text(_schema_markdown(), encoding="utf-8")
    (output / "README.md").write_text(_readme(), encoding="utf-8")

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    province_tree_counts = {
        province: sum(1 for tree in trees if tree.jurisdiction == province)
        for province in PROVINCES
    }
    add_check(
        "thirteen_jurisdictions",
        len({tree.jurisdiction for tree in trees}) == 13,
        f"{len({tree.jurisdiction for tree in trees})} jurisdictions present",
    )
    add_check(
        "ten_trees_per_province",
        all(count == N_TREES_PER_PROVINCE for count in province_tree_counts.values()),
        "; ".join(
            f"{province}={count}" for province, count in province_tree_counts.items()
        ),
    )
    add_check(
        "volume_balance",
        totals["balance_error_m3"] <= 1e-6,
        f"national balance error {totals['balance_error_m3']:.3g} m3",
    )
    add_check(
        "net_defect_balance",
        totals["net_plus_defect_error_m3"] <= 1e-6,
        f"national net+defect error {totals['net_plus_defect_error_m3']:.3g} m3",
    )
    all_numeric_frames = [
        pd.DataFrame(tree_rows),
        pd.DataFrame(log_rows),
        pd.DataFrame(residue_rows),
        pd.DataFrame(reconciliation_rows),
        pd.DataFrame(province_rows),
        pd.DataFrame(comparison_rows),
    ]
    add_check(
        "no_nan",
        all(
            not frame.select_dtypes(include="number").isna().any().any()
            for frame in all_numeric_frames
        ),
        "numeric columns contain no NaN",
    )
    add_check(
        "no_negative",
        all(
            (frame.select_dtypes(include="number") >= 0.0).all().all()
            for frame in all_numeric_frames
        ),
        "numeric columns are non-negative",
    )
    add_check(
        "grade_not_assigned",
        all(log.grade == "not_assigned" for log in logs)
        and all(log.grade_status == "not_assigned" for log in logs),
        f"{len(logs)} log rows grade=not_assigned",
    )
    add_check(
        "taper_evidence",
        all(log.taper_evidence == "published_table" for log in logs)
        and all(residue.taper_evidence == "published_table" for residue in residues),
        "all taper rows are published_table",
    )
    local_jurisdictions = sorted(
        {
            tree.jurisdiction
            for tree in trees
            if extras[tree.tree_id]["taper_status"] == "local_published"
        }
    )
    donor_jurisdictions = sorted(
        {
            tree.jurisdiction
            for tree in trees
            if extras[tree.tree_id]["taper_status"] == "donor_published"
        }
    )
    add_check(
        "taper_dispatch",
        local_jurisdictions == ["BC", "QC"]
        and set(donor_jurisdictions) == set(PROVINCES) - {"BC", "QC"},
        f"local={local_jurisdictions}; donor={donor_jurisdictions}",
    )
    add_check(
        "provenance",
        all(tree.provenance.startswith("representative_synthetic_v1") for tree in trees)
        and all(
            extras[tree.tree_id]["tree_list_basis"]
            in {"nfi_coverage_composition", "documented_assumption"}
            for tree in trees
        ),
        "every tree carries representative_synthetic_v1 provenance and a basis label",
    )
    add_check(
        "coefficient_comparison",
        len(comparison_rows) == len(PROVINCES),
        f"{len(comparison_rows)} province comparison rows",
    )
    add_check(
        "labels",
        all(
            row["model_estimate"]
            and row["synthetic"]
            and not row["observed"]
            and not row["policy_grade"]
            and not row["national_claim"]
            for row in (
                tree_rows
                + log_rows
                + residue_rows
                + reconciliation_rows
                + province_rows
                + comparison_rows
            )
        ),
        "model_estimate/synthetic/observed=false/policy_grade=false/national_claim=false",
    )
    add_check(
        "repository_hygiene",
        not deletions,
        "no unexpected D in parent or data git status before run",
    )

    failed = [check for check in checks if check["status"] != "PASS"]
    if failed:
        raise SchemaError(f"pilot checks failed: {failed}")

    summary = {
        "artifact": "national_bucking_pilot_v1",
        "schema_version": "national_bucking_pilot_v1",
        "scope": "representative_synthetic_pilot",
        "national_claim": False,
        "labels": {
            "model_estimate": True,
            "synthetic": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "ui_or_public_osecan_changed": False,
        },
        "counts": {
            "jurisdictions": len({tree.jurisdiction for tree in trees}),
            "trees": len(trees),
            "logs": len(logs),
            "residue_rows": len(residues),
            "reconciliation_rows": len(reconciliation_rows),
            "province_summary_rows": len(province_rows),
            "coefficient_comparison_rows": len(comparison_rows),
            "trees_per_province": N_TREES_PER_PROVINCE,
        },
        "totals": totals,
        "province_summary": province_rows,
        "taper_coverage": {
            "local_jurisdictions": local_jurisdictions,
            "donor_jurisdictions": donor_jurisdictions,
            "n_local_trees": sum(
                1
                for tree in trees
                if extras[tree.tree_id]["taper_status"] == "local_published"
            ),
            "n_donor_trees": sum(
                1
                for tree in trees
                if extras[tree.tree_id]["taper_status"] == "donor_published"
            ),
            "taper_ids": sorted({taper.taper_id for taper in tapers.values()}),
        },
        "evidence": {
            "tree_tiers": ["assumption"],
            "log_taper_tiers": ["published_table"],
            "log_row_tiers": sorted({log.evidence for log in logs}),
            "residue_row_tiers": sorted({residue.evidence for residue in residues}),
            "taper_status_values": sorted(
                {extras[tree.tree_id]["taper_status"] for tree in trees}
            ),
        },
        "checks": checks,
        "limitations": [
            "Tree lists are representative synthetic draws, not inventory, PSP data, or an NFI expansion.",
            "AB/ON/NS/NU species composition is a documented assumption because the open NFI coverage table has no provincial rows for them.",
            "Non-QC/BC trees use a cross-jurisdiction donor taper; donor BEC anchors are BC anchors, not local BEC zones.",
            "Dimension-only pilot sorts are not official federal or provincial log grades; grade=not_assigned.",
            "Prices, density, moisture, chip and hog yields are pilot assumptions.",
            "Facility coefficient comparison is screening only; harvest residue and facility process residue are different streams.",
            "No national, operational, policy-grade, AAC, or investment claim is made.",
            "No UI or public OSeCAN surface was changed.",
        ],
    }

    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(
        _report_markdown(summary, province_rows, comparison_rows), encoding="utf-8"
    )

    output_files = [
        "README.md",
        "schema.md",
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "province_summary.csv",
        "coefficient_comparison.csv",
        "summary.json",
        "report.md",
        "taper_dispatch.csv",
        "sort_rules.csv",
    ] + [f"tree_lists/{province}.csv" for province in PROVINCES]
    manifest = {
        "artifact": "national_bucking_pilot_v1",
        "schema_version": "national_bucking_pilot_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "scope": "representative_synthetic_pilot",
        "national_claim": False,
        "labels": summary["labels"],
        "command": (
            ".venv/bin/python -m workflows.national.bucking.national_bucking_pilot_v1 "
            "--out-dir data/derived/national/bucking/national_bucking_pilot_v1"
        ),
        "config": {
            "provinces": list(PROVINCES),
            "trees_per_province": N_TREES_PER_PROVINCE,
            "max_species_per_province": MAX_SPECIES_PER_PROVINCE,
            "target_species_coverage": TARGET_SPECIES_COVERAGE,
            "stump_height_m": STUMP_HEIGHT_M,
            "moisture_wet_basis": MOISTURE_WET_BASIS,
            "chip_yield_fraction": CHIP_YIELD_FRACTION,
            "hog_yield_fraction": HOG_YIELD_FRACTION,
            "price_deck_id": PRICE_DECK_ID,
            "basic_density_odt_m3": BASIC_DENSITY_ODT_M3,
            "donor_policy": DONOR_POLICY,
            "bec_anchor": BEC_ANCHOR,
            "bucker": "value-maximising dynamic program over national pilot target lengths",
        },
        "inputs": {
            "nfi_coverage": {
                "path": NFI_COVERAGE_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(NFI_COVERAGE_PATH),
            },
            "nfi_height_models": {
                "path": NFI_HEIGHT_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(NFI_HEIGHT_PATH),
            },
            "facility_product_chain": {
                "path": FACILITY_CHAIN_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(FACILITY_CHAIN_PATH),
            },
            "qc_taper_registry": {
                "path": QC_TAPER_PATH.relative_to(REPO_ROOT).as_posix(),
                "sha256": _sha256(QC_TAPER_PATH),
            },
            "kozak_parameters": {
                "path": f"nemora/config/kozak/{kozak_path.name}",
                "sha256": _sha256(kozak_path),
            },
        },
        "code": [
            "workflows/national/bucking/national_bucking_pilot_v1.py",
            "workflows/national/bucking/pilot_schema.py",
            "workflows/national/bucking/schneider2013.py",
            "workflows/national/bucking/taper.py",
            "workflows/national/bucking/rules/national_pilot_sorts.csv",
            "workflows/national/bucking/rules/qc_schneider2013_taper.csv",
        ],
        "counts": summary["counts"],
        "totals": totals,
        "checks": checks,
        "limitations": summary["limitations"],
        "outputs": [
            {
                "path": name,
                "sha256": _sha256(output / name),
                "bytes": (output / name).stat().st_size,
            }
            for name in output_files
        ],
        "evidence_tiers": list(EVIDENCE_TIERS),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    manifest = generate_pilot(args.out_dir)
    totals = manifest["totals"]
    print(
        f"pilot v1 wrote {args.out_dir} | provinces={manifest['counts']['jurisdictions']} "
        f"trees={manifest['counts']['trees']} logs={manifest['counts']['logs']} "
        f"taper={totals['taper_total_m3']:.3f} m3 residue={totals['residue_m3']:.3f} m3 "
        f"balance_error={totals['balance_error_m3']:.3g} m3"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
