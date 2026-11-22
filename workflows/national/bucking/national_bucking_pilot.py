"""National bucking Phase 0/2 pilot for one non-BC jurisdiction (Quebec).

Scope
-----
This is a **pilot**, not a national supply estimate.  It exercises the
national contracts (tree list, taper registry, grade rule set, ``BuckedLog``,
``ResidueLedger``) end to end on a small synthetic Quebec tree list with the
published Schneider (2013) fixed-effect taper.  The tree geometry is synthetic
and labelled as such; the taper is a published Quebec model; the dimension
sorts, prices and residue assumptions are pilot values, not official Quebec
grades or market prices.  No result here is a national or operational claim.

The bucking optimiser is a small dynamic program over a fixed candidate-length
set.  It maximises net log value subject to each sort's minimum small-end
diameter and exact target length.  Residue is defined as
``taper total - recovered gross log volume`` so the volume balance is explicit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    Schneider2013Taper,
    load_taper_registry,
    registry_records,
)

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "PilotSortRule",
    "buck_tree",
    "build_synthetic_tree_list",
    "generate_pilot",
    "load_pilot_sorts",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "derived" / "national" / "bucking" / "national_bucking_pilot_v0"
TAPER_REGISTRY_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "qc_schneider2013_taper.csv"
)
SORT_RULES_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "qc_pilot_sorts.csv"
)

MOISTURE_WET_BASIS = 0.45
CHIP_YIELD_FRACTION = 0.85
HOG_YIELD_FRACTION = 0.10

#: Rounded typical basic densities (ODT/m3).  These are planning assumptions,
#: not a Quebec sample; each residue row labels them ``assumption``.
BASIC_DENSITY_ODT_M3: dict[str, float] = {
    "BOP": 0.50,
    "EPB": 0.37,
    "EPN": 0.42,
    "EPR": 0.38,
    "PEG": 0.35,
    "PET": 0.35,
    "SAB": 0.34,
    "THO": 0.29,
}

COMMON_NAMES: dict[str, str] = {
    "BOP": "paper birch",
    "EPB": "white spruce",
    "EPN": "black spruce",
    "EPR": "red spruce",
    "PEG": "large-tooth aspen",
    "PET": "trembling aspen",
    "SAB": "balsam fir",
    "THO": "eastern white cedar",
}

DECIDUOUS = {"BOP", "PEG", "PET"}


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


def load_pilot_sorts(path: Path = SORT_RULES_PATH) -> tuple[PilotSortRule, ...]:
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
        raise SchemaError(f"no pilot sort rules found in {path}")
    return tuple(sorted(rules, key=lambda rule: rule.priority))


def _rules_for_species(
    rules: Iterable[PilotSortRule], species: str
) -> tuple[PilotSortRule, ...]:
    group = "deciduous" if species in DECIDUOUS else "conifer"
    selected = tuple(
        rule for rule in rules if rule.species_group in {"all", group}
    )
    if not selected:
        raise SchemaError(f"no sort rules for species {species} ({group})")
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
    taper: Schneider2013Taper,
    tree: PilotTree,
    rules: tuple[PilotSortRule, ...],
    lengths: tuple[float, ...],
    position_m: float,
    memo: dict[float, tuple[float, tuple[_Cut, ...]]],
    depth: int = 0,
) -> tuple[float, tuple[_Cut, ...]]:
    """Return the best additional net value and cut list from ``position_m``."""
    key = round(position_m, 6)
    if key in memo:
        return memo[key]
    if depth > 32:
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
            tree.dbh_cm, tree.height_m, position_m, top, intervals=48
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
    taper: Schneider2013Taper,
    rules: tuple[PilotSortRule, ...],
) -> tuple[list[PilotBuckedLog], PilotResidueLedger]:
    """Buck one stem and return its logs and residue ledger."""
    if tree.status != "live":
        raise SchemaError(f"tree {tree.tree_id} is {tree.status!r}; only live trees are bucked")
    if tree.taper_id != taper.taper_id:
        raise SchemaError(
            f"tree {tree.tree_id} taper_id {tree.taper_id!r} does not match "
            f"resolved taper {taper.taper_id!r}"
        )
    active_rules = _rules_for_species(rules, tree.species)
    lengths = tuple(sorted({rule.target_length_m for rule in active_rules}, reverse=True))
    total_volume = taper.total_volume_m3(tree.dbh_cm, tree.height_m)
    _, cuts = _solve(
        taper,
        tree,
        active_rules,
        lengths,
        DEFAULT_STUMP_HEIGHT_M,
        {},
    )

    logs: list[PilotBuckedLog] = []
    recovered_gross = 0.0
    recovered_net = 0.0
    defect_total = 0.0
    for index, cut in enumerate(cuts, start=1):
        large_dib = taper.diameter_inside_bark_cm(
            tree.dbh_cm, tree.height_m, cut.start_m
        )
        small_dib = taper.diameter_inside_bark_cm(tree.dbh_cm, tree.height_m, cut.end_m)
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
                evidence=weakest_evidence((taper.evidence, cut.rule.evidence, "assumption", tree.evidence)),
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
    tolerance = max(1e-9, 1e-8 * total_volume)
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
            f"{recovered_gross:.6f} m3; includes stump below "
            f"{DEFAULT_STUMP_HEIGHT_M:.2f} m, top, and unmerchantable sections. "
            f"Defect deduction in recovered logs {defect_total:.6f} m3; "
            f"density {density:.2f} ODT/m3, moisture {MOISTURE_WET_BASIS:.2f} wet "
            f"basis, chip yield {CHIP_YIELD_FRACTION:.2f}, hog yield "
            f"{HOG_YIELD_FRACTION:.2f} are pilot assumptions. Taper: "
            f"{taper.taper_id} ({taper.evidence})."
        ),
    )
    return logs, residue


def build_synthetic_tree_list() -> tuple[PilotTree, ...]:
    """Hand-authored Quebec species/DBH/height grid.

    The values are a deliberate synthetic test grid, not measurements.  Every
    row says so in ``provenance`` and carries ``evidence=assumption``.
    """
    specs: tuple[tuple[str, float, float], ...] = (
        ("BOP", 18.0, 17.0),
        ("BOP", 26.0, 21.0),
        ("BOP", 34.0, 25.0),
        ("EPB", 20.0, 17.0),
        ("EPB", 30.0, 23.0),
        ("EPB", 40.0, 28.0),
        ("EPN", 12.0, 13.0),
        ("EPN", 20.0, 18.0),
        ("EPN", 28.0, 23.0),
        ("EPR", 16.0, 16.0),
        ("EPR", 24.0, 20.0),
        ("EPR", 32.0, 25.0),
        ("PEG", 22.0, 20.0),
        ("PEG", 32.0, 26.0),
        ("PEG", 42.0, 30.0),
        ("PET", 16.0, 18.0),
        ("PET", 26.0, 23.0),
        ("PET", 36.0, 28.0),
        ("SAB", 12.0, 12.0),
        ("SAB", 20.0, 17.0),
        ("SAB", 28.0, 22.0),
        ("THO", 14.0, 11.0),
        ("THO", 22.0, 15.0),
        ("THO", 30.0, 19.0),
    )
    trees: list[PilotTree] = []
    for index, (species, dbh, height) in enumerate(specs, start=1):
        defect = (index % 7) * 2.5  # 0 to 15 percent, deterministic
        trees.append(
            PilotTree(
                tree_id=f"QC-PILOT-T{index:03d}",
                site_id="QC-PILOT-S01",
                jurisdiction="QC",
                species=species,
                species_group="deciduous" if species in DECIDUOUS else "conifer",
                dbh_cm=dbh,
                height_m=height,
                defect_pct=defect,
                expansion_factor=1.0,
                status="live",
                provenance=(
                    "synthetic_pilot_v0: hand-authored Quebec species/DBH/height "
                    "grid for schema and taper-contract testing; not measured; "
                    "no NFI/PSP link"
                ),
                evidence="assumption",
                taper_id=f"QC-SCHNEIDER2013-{species}",
            )
        )
    return tuple(trees)


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> int:
    materialized = list(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(row)
    return len(materialized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_markdown() -> str:
    return """# National bucking pilot schema v0

This file documents the Phase 0/2 records emitted by
`workflows/national/bucking/national_bucking_pilot.py`.  The records are a
pilot contract; field names follow the national architecture note
(`planning/national-bucking-supply-architecture.md`) and are not a claim that
the schema is final.

## Evidence tiers

Every row carries an `evidence` value.  The allowed values, strongest first,
are `measured_taper`, `published_table` and `assumption`.  A row's `evidence`
is the weakest tier among its components; component tiers are retained in
separate columns so a consumer can see what is measured and what is assumed.

| Tier | Meaning in this pilot |
|---|---|
| `measured_taper` | Coefficients or measurements tied to the individual stem. Not used in this pilot. |
| `published_table` | A published equation or coefficient table applied to the tree list. Used for the Schneider (2013) taper. |
| `assumption` | A clearly labelled planning value: synthetic tree geometry, dimension sorts, prices, density, moisture, chip and hog yield. |

## `tree_list.csv`

| Field | Type | Notes |
|---|---|---|
| `tree_id` | text | Stable tree identifier. |
| `site_id` | text | Pilot site identifier. |
| `jurisdiction` | text | `QC` for this pilot. |
| `species` | text | Quebec species code used by the taper registry. |
| `species_group` | text | `conifer` or `deciduous`. |
| `dbh_cm` | float | DBH outside bark, cm. Synthetic in v0. |
| `height_m` | float | Total height, m. Synthetic in v0. |
| `defect_pct` | float | Defect deduction applied to log net volume, percent of gross. |
| `expansion_factor` | float | Trees per hectare; 1.0 for the unweighted pilot grid. |
| `status` | text | `live` or `dead`; only live trees are bucked. |
| `provenance` | text | Source statement. Synthetic in v0. |
| `evidence` | text | Evidence tier for the tree geometry. |
| `taper_id` | text | Registry key resolved for the tree. |

## `bucked_logs.csv`

| Field | Type | Notes |
|---|---|---|
| `tree_id` | text | Tree identifier. |
| `jurisdiction` | text | `QC`. |
| `log_index` | integer | 1-based, from stump upward. |
| `length_m` | float | Log length. |
| `large_end_dib_cm` | float | Inside-bark diameter at the lower end. |
| `small_end_dib_cm` | float | Inside-bark diameter at the upper end. |
| `volume_m3` | float | Gross inside-bark log volume. |
| `net_volume_m3` | float | Gross volume after the tree defect deduction. |
| `defect_deduction_m3` | float | `volume_m3 - net_volume_m3`. |
| `sort_code` | text | Pilot dimension sort code. |
| `product` | text | `sawlog`, `peeler`, `pulpwood` or `other`. |
| `grade` | text | `not_assigned` in v0: no official grade evidence. |
| `grade_status` | text | `not_assigned`. |
| `sort_basis` | text | `dimension_only`. |
| `price_cad_m3` | float | Pilot price, CAD per net m3. |
| `value_cad` | float | `net_volume_m3 * price_cad_m3`. |
| `evidence` | text | Weakest component tier. |
| `taper_evidence` | text | Tier of the taper geometry. |
| `sort_evidence` | text | Tier of the sort rule. |
| `value_evidence` | text | Tier of the price deck. |
| `tree_evidence` | text | Tier of the tree geometry. |
| `taper_id` | text | Registry key. |
| `taper_source` | text | Published taper citation. |
| `grade_rule_id` | text | Rule-set row that selected the sort. |
| `price_deck_id` | text | Versioned pilot deck identifier. |
| `provenance` | text | Tree provenance carried forward. |

## `residue_ledger.csv`

| Field | Type | Notes |
|---|---|---|
| `tree_id` | text | Tree identifier. |
| `jurisdiction` | text | `QC`. |
| `species` | text | Species code. |
| `residue_volume_m3` | float | Taper total minus recovered gross logs. |
| `basic_density_odt_m3` | float | Oven-dry tonnes per m3, pilot assumption. |
| `residue_odt` | float | `residue_volume_m3 * basic_density_odt_m3`. |
| `moisture_wet_basis` | float | Wet-basis moisture, pilot assumption. |
| `green_tonnes` | float | `residue_odt / (1 - moisture)`. |
| `chip_yield_fraction` | float | Green chip fraction, pilot assumption. |
| `chip_green_tonnes` | float | `green_tonnes * chip_yield_fraction`. |
| `hog_yield_fraction` | float | Green hog fraction, pilot assumption. |
| `hog_green_tonnes` | float | `green_tonnes * hog_yield_fraction`. |
| `recovered_log_m3` | float | Recovered gross log volume. |
| `taper_total_m3` | float | Taper total from stump height 0.3 m to tip. |
| `balance_error_m3` | float | `abs(recovered + residue - taper total)`. |
| `evidence` | text | Weakest component tier. |
| `taper_evidence` | text | Tier of the taper geometry. |
| `density_evidence` | text | Tier of the density assumption. |
| `moisture_evidence` | text | Tier of the moisture assumption. |
| `chip_evidence` | text | Tier of the chip/hog assumptions. |
| `provenance` | text | Tree provenance carried forward. |
| `notes` | text | Balance and assumption statement. |

## `reconciliation.csv`

One row per tree: taper total, recovered gross, residue, defect, net recovered,
log count, product volumes, value, the balance error, and the row evidence tier.
Pilot totals are in
`reconciliation_summary.json` and `manifest.json`; the CSV has no aggregate
row, so every numeric field is a real tree value.

## Taper registry and grade rules

`taper_registry.csv` is the registry actually used.  `grade_rules.csv` is the
pilot dimension rule set actually used.  Both are versioned data products, not
code branches.
"""


def _readme(output_dir: Path) -> str:
    return """# National bucking pilot v0 — Quebec

**Scope: pilot.**  This directory is a Phase 0/2 pilot for one non-BC
jurisdiction (Quebec).  It is not a national supply estimate, not an
operational grade recovery, and not a validation of the taper outside its
published Quebec domain.

## What ran

1. A 24-tree synthetic Quebec tree list (`tree_list.csv`).  Species, DBH and
   height are hand-authored for contract testing; every row says
   `synthetic_pilot_v0` and `assumption`.
2. The published Schneider (2013) fixed-effect tree taper for eight Quebec
   species (`taper_registry.csv`), implemented in
   `workflows/national/bucking/schneider2013.py` from the CFSForestTools
   parameter tables (LGPL-3.0).  Every log records `taper_evidence`.
3. A pilot dimension-only sort rule set (`grade_rules.csv`).  It is informed by
   Quebec MERIS/Petro product dimensions but is **not** an official Quebec log
   grade.  `grade=not_assigned` and `sort_basis=dimension_only`.
4. A dynamic-programming bucker that maximises net log value over fixed
   candidate lengths.
5. A residue ledger with volume, ODT, moisture, chip and hog yield.  Density,
   moisture, chip and hog values are labelled assumptions.

## Files

| File | Content |
|---|---|
| `schema.md` | Field-by-field schema and evidence-tier rules. |
| `tree_list.csv` | Input tree list. |
| `bucked_logs.csv` | One row per recovered log. |
| `residue_ledger.csv` | One row per stem. |
| `reconciliation.csv` | Per-tree and total volume reconciliation. |
| `reconciliation_summary.json` | Machine-readable totals and checks. |
| `taper_registry.csv` | Taper registry used by the run. |
| `grade_rules.csv` | Pilot sort/price rule set used by the run. |
| `manifest.json` | Code, configuration, checksums, counts and scope. |

## What is a pilot vs national

- **Pilot:** the schema wiring, taper dispatch, bucking DP, residue accounting,
  and reconciliation on a small synthetic Quebec tree list.
- **Not national:** the tree list is synthetic and unweighted; the sort set and
  prices are assumptions; there is no provincial inventory expansion, no
  official grade assignment, and no national aggregation.  Do not cite these
  volumes as Quebec or Canada supply.

## Reproduce

```bash
.venv/bin/python -m workflows.national.bucking.national_bucking_pilot \
  --out-dir data/derived/national/bucking/national_bucking_pilot_v0
```
"""


def generate_pilot(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Run the pilot and write the output directory.  Returns the manifest."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    registry = load_taper_registry(TAPER_REGISTRY_PATH)
    rules = load_pilot_sorts(SORT_RULES_PATH)
    trees = build_synthetic_tree_list()

    logs: list[PilotBuckedLog] = []
    residues: list[PilotResidueLedger] = []
    tree_rows: list[dict[str, Any]] = []
    for tree in trees:
        taper = registry.resolve(tree.jurisdiction, tree.species)
        tree_logs, residue = buck_tree(tree, taper, rules)
        logs.extend(tree_logs)
        residues.append(residue)
        tree_rows.append(tree.to_dict())

    if not logs:
        raise SchemaError("pilot produced no logs")

    tree_columns = list(tree_rows[0])
    log_columns = list(logs[0].to_dict())
    residue_columns = list(residues[0].to_dict())

    tree_count = _write_csv(output / "tree_list.csv", tree_rows, tree_columns)
    log_count = _write_csv(output / "bucked_logs.csv", [row.to_dict() for row in logs], log_columns)
    residue_count = _write_csv(
        output / "residue_ledger.csv", [row.to_dict() for row in residues], residue_columns
    )

    tree_by_id = {tree.tree_id: tree for tree in trees}
    logs_by_tree: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by_tree.setdefault(log.tree_id, []).append(log)

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
                "balance_error_m3": abs(
                    residue.recovered_log_m3
                    + residue.residue_volume_m3
                    - residue.taper_total_m3
                ),
                "evidence": weakest_evidence(
                    [residue.evidence, *(log.evidence for log in tree_logs)]
                ),
                "taper_evidence": residue.taper_evidence,
            }
        )

    totals = {
        "n_trees": len(trees),
        "n_logs": len(logs),
        "taper_total_m3": sum(row["taper_total_m3"] for row in reconciliation_rows),
        "recovered_gross_m3": sum(row["recovered_gross_m3"] for row in reconciliation_rows),
        "residue_m3": sum(row["residue_m3"] for row in reconciliation_rows),
        "defect_m3": sum(row["defect_m3"] for row in reconciliation_rows),
        "recovered_net_m3": sum(row["recovered_net_m3"] for row in reconciliation_rows),
        "value_cad": sum(row["value_cad"] for row in reconciliation_rows),
        "residue_odt": sum(residue.residue_odt for residue in residues),
        "chip_green_tonnes": sum(residue.chip_green_tonnes for residue in residues),
        "hog_green_tonnes": sum(residue.hog_green_tonnes for residue in residues),
        "sawlog_m3": sum(row["sawlog_m3"] for row in reconciliation_rows),
        "pulpwood_m3": sum(row["pulpwood_m3"] for row in reconciliation_rows),
        "other_m3": sum(row["other_m3"] for row in reconciliation_rows),
        "max_balance_error_m3": max(row["balance_error_m3"] for row in reconciliation_rows),
    }
    totals["balance_error_m3"] = abs(
        totals["recovered_gross_m3"] + totals["residue_m3"] - totals["taper_total_m3"]
    )
    totals["net_plus_defect_error_m3"] = abs(
        totals["recovered_net_m3"] + totals["defect_m3"] - totals["recovered_gross_m3"]
    )
    if totals["balance_error_m3"] > 1e-8:
        raise SchemaError(
            f"pilot volume balance failed: {totals['balance_error_m3']:.12g} m3"
        )
    if totals["net_plus_defect_error_m3"] > 1e-8:
        raise SchemaError(
            f"pilot net/defect balance failed: {totals['net_plus_defect_error_m3']:.12g} m3"
        )

    reconciliation_columns = list(reconciliation_rows[0])
    reconciliation_count = _write_csv(
        output / "reconciliation.csv", reconciliation_rows, reconciliation_columns
    )

    _write_csv(
        output / "taper_registry.csv",
        registry_records(registry),
        list(registry_records(registry)[0]),
    )
    rule_records = [asdict(rule) for rule in rules]
    _write_csv(output / "grade_rules.csv", rule_records, list(rule_records[0]))
    (output / "schema.md").write_text(_schema_markdown(), encoding="utf-8")
    (output / "README.md").write_text(_readme(output), encoding="utf-8")

    summary = {
        "scope": "pilot",
        "jurisdiction": "QC",
        "national_claim": False,
        "totals": totals,
        "counts": {
            "trees": tree_count,
            "logs": log_count,
            "residue_rows": residue_count,
            "reconciliation_rows": reconciliation_count,
        },
        "checks": {
            "volume_balance_m3": totals["balance_error_m3"],
            "volume_balance_passed": totals["balance_error_m3"] <= 1e-8,
            "net_defect_balance_m3": totals["net_plus_defect_error_m3"],
            "net_defect_balance_passed": totals["net_plus_defect_error_m3"] <= 1e-8,
        },
        "evidence": {
            "log_row_tiers": sorted({log.evidence for log in logs}),
            "log_taper_tiers": sorted({log.taper_evidence for log in logs}),
            "residue_row_tiers": sorted({residue.evidence for residue in residues}),
            "tree_tiers": sorted({tree.evidence for tree in trees}),
        },
        "products": {
            "sawlog_m3": totals["sawlog_m3"],
            "pulpwood_m3": totals["pulpwood_m3"],
            "other_m3": totals["other_m3"],
        },
        "residue": {
            "residue_odt": totals["residue_odt"],
            "chip_green_tonnes": totals["chip_green_tonnes"],
            "hog_green_tonnes": totals["hog_green_tonnes"],
            "moisture_wet_basis": MOISTURE_WET_BASIS,
            "chip_yield_fraction": CHIP_YIELD_FRACTION,
            "hog_yield_fraction": HOG_YIELD_FRACTION,
        },
        "limitations": [
            "Tree list is synthetic and unweighted; not Quebec inventory.",
            "Pilot dimension sorts are not official Quebec log grades.",
            "Prices, density, moisture, chip and hog yields are assumptions.",
            "No national or operational supply claim is made.",
        ],
    }
    (output / "reconciliation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    output_files = [
        "README.md",
        "schema.md",
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "reconciliation_summary.json",
        "taper_registry.csv",
        "grade_rules.csv",
    ]
    manifest = {
        "schema_version": "national_bucking_pilot_v0",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "scope": "pilot",
        "jurisdiction": "QC",
        "national_claim": False,
        "phase": "Phase 0/2 pilot",
        "command": (
            ".venv/bin/python -m workflows.national.bucking.national_bucking_pilot "
            "--out-dir data/derived/national/bucking/national_bucking_pilot_v0"
        ),
        "config": {
            "taper_registry": TAPER_REGISTRY_PATH.relative_to(REPO_ROOT).as_posix(),
            "grade_rules": SORT_RULES_PATH.relative_to(REPO_ROOT).as_posix(),
            "stump_height_m": DEFAULT_STUMP_HEIGHT_M,
            "basic_density_odt_m3": BASIC_DENSITY_ODT_M3,
            "moisture_wet_basis": MOISTURE_WET_BASIS,
            "chip_yield_fraction": CHIP_YIELD_FRACTION,
            "hog_yield_fraction": HOG_YIELD_FRACTION,
            "bucker": "value-maximising dynamic program over registry target lengths",
        },
        "code": [
            "workflows/national/bucking/schneider2013.py",
            "workflows/national/bucking/pilot_schema.py",
            "workflows/national/bucking/national_bucking_pilot.py",
            "workflows/national/bucking/rules/qc_schneider2013_taper.csv",
            "workflows/national/bucking/rules/qc_pilot_sorts.csv",
        ],
        "inputs": {
            "tree_list": "synthetic in national_bucking_pilot.build_synthetic_tree_list",
            "taper": "Schneider (2013) fixed-effect tree model, CFSForestTools parameter tables (LGPL-3.0)",
        },
        "counts": summary["counts"],
        "totals": totals,
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
        f"pilot wrote {args.out_dir} | trees={manifest['counts']['trees']} "
        f"logs={manifest['counts']['logs']} taper={totals['taper_total_m3']:.3f} m3 "
        f"residue={totals['residue_m3']:.3f} m3 "
        f"balance_error={totals['balance_error_m3']:.3g} m3"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
