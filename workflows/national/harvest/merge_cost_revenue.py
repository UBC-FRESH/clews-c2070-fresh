"""Merge the BC BCTS and QC MERIS cost/revenue blocks and report coverage.

The two inputs are already in the canonical 16-field ledger schema.  This
module concatenates them without re-keying, re-weighting, imputing, or
cross-province aggregation.  Each block is validated on its own before the
merge and the merged file is validated again with
:func:`workflows.national.harvest.cost_revenue.load_ledger`.

Outputs under ``data/derived/national/harvest_basis/forest_cost_revenue/``:

* ``forest_cost_revenue.csv`` -- the merged audit ledger;
* ``coverage_summary.json`` -- machine-readable row counts and coverage math;
* ``FOREST_COST_REVENUE_COVERAGE.md`` -- the human-readable coverage report.

The coverage report is deliberately explicit that the ledger is a selected
sample.  It never scales BCTS to BC, MERIS to QC, or BC/QC to Canada, and it
never adds the Crown and forest-sector perspectives together.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workflows.national.harvest.cost_revenue import (
    LEDGER_FIELDS,
    Component,
    Confidence,
    Ledger,
    LedgerValidationError,
    Perspective,
    Unit,
    load_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"

BC_BLOCK_PATH = HARVEST_BASIS_DIR / "bcts_bc/bcts_bid_ledger.csv"
QC_BLOCK_PATH = HARVEST_BASIS_DIR / "meris_qc_sample/forest_cost_revenue_sample.csv"
BCTS_SUMMARY_PATH = HARVEST_BASIS_DIR / "bcts_bc/bcts_sales_summary.json"
BCTS_RESULTS_PROVENANCE_PATH = (
    HARVEST_BASIS_DIR / "bcts_bc/bcts_bid_results_provenance.json"
)
HARVEST_TEMPLATE_PATH = HARVEST_BASIS_DIR / "harvest_basis_template.csv"

DEFAULT_OUTPUT_DIR = HARVEST_BASIS_DIR / "forest_cost_revenue"
DEFAULT_OUTPUT_CSV = DEFAULT_OUTPUT_DIR / "forest_cost_revenue.csv"
DEFAULT_COVERAGE_JSON = DEFAULT_OUTPUT_DIR / "coverage_summary.json"
DEFAULT_COVERAGE_MD = DEFAULT_OUTPUT_DIR / "FOREST_COST_REVENUE_COVERAGE.md"

# Denominators from the 2024 harvest template (harvest_basis_template.csv).
# The brief's rounded values are 118.98M national, 38.88M BC, 25.94M QC.
NATIONAL_HARVEST_2024_M3 = 118_977_339.0
BC_HARVEST_2024_M3 = 38_876_568.0
QC_HARVEST_2024_M3 = 25_939_056.0

# MERIS table counts, from meris_qc_sample/tables_key.csv and README.md.
QC_TARIFICATION_ZONES = 191
QC_STUMPAGE_RATE_ROWS = 2_674
QC_TREATMENT_ROWS = 94

BLOCK_SPECS: tuple[tuple[Path, str], ...] = (
    (BC_BLOCK_PATH, "BC"),
    (QC_BLOCK_PATH, "QC"),
)

NET_VALUE_COMPONENTS: tuple[Component, ...] = (
    Component.DELIVERED_LOG_REVENUE,
    Component.RESIDUE_REVENUE,
    Component.STUMPAGE_BASE,
    Component.STUMPAGE_BID_PREMIUM,
    Component.STUMPAGE_TOTAL,
    Component.HARVEST,
    Component.ROAD,
    Component.HAUL,
    Component.SILVICULTURE,
    Component.RESIDUE_RECOVERY,
    Component.OVERHEAD,
    Component.ADMIN_FEE,
    Component.CROWN_OTHER,
    Component.CROWN_CREDIT,
)

COMPONENT_ORDER = {component.value: index for index, component in enumerate(Component)}


@dataclass(frozen=True, slots=True)
class LedgerBlock:
    path: Path
    jurisdiction: str
    rows: tuple[dict[str, str], ...]
    sha256: str

    def __len__(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class MergeResult:
    ledger: Ledger
    output_path: Path
    coverage_json_path: Path
    coverage_md_path: Path
    summary: Mapping[str, object]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ledger_block(path: Path, expected_jurisdiction: str) -> LedgerBlock:
    """Read a block without changing any cell value.

    The header must be exactly the canonical schema and every row must belong
    to the expected jurisdiction.  Deeper row validation is delegated to
    ``load_ledger`` so there is one source of truth for the schema.
    """
    if not path.exists():
        raise FileNotFoundError(f"ledger block does not exist: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        if header is None:
            raise LedgerValidationError(f"{path} is empty; expected a header")
        if tuple(header) != LEDGER_FIELDS:
            raise LedgerValidationError(
                f"{path} header {header!r} does not match the canonical "
                f"16-field schema {list(LEDGER_FIELDS)!r}"
            )
        rows: list[dict[str, str]] = []
        for line_number, record in enumerate(reader, start=2):
            extra = record.get(None)
            if extra is not None:
                raise LedgerValidationError(
                    f"{path} line {line_number}: row has {len(extra)} value(s) "
                    "beyond the 16 fields"
                )
            row = {name: (record[name] or "").strip() for name in LEDGER_FIELDS}
            if not any(row.values()):
                continue
            if row["jurisdiction"] != expected_jurisdiction:
                raise LedgerValidationError(
                    f"{path} line {line_number}: expected jurisdiction "
                    f"{expected_jurisdiction}, got {row['jurisdiction']!r}"
                )
            rows.append(row)

    if not rows:
        raise LedgerValidationError(f"{path} has no ledger rows")
    load_ledger(path)
    return LedgerBlock(
        path=path,
        jurisdiction=expected_jurisdiction,
        rows=tuple(rows),
        sha256=sha256_file(path),
    )


def ledger_key(row: Mapping[str, str]) -> tuple[str, ...]:
    return (
        row["jurisdiction"],
        row["source"],
        row["year"],
        row["spatial_unit"],
        row["species"],
        row["product"],
        row["perspective"],
        row["component"],
        row["licence"],
    )


def coverage_key(row: Mapping[str, str]) -> tuple[str, ...]:
    return (
        row["jurisdiction"],
        row["year"],
        row["spatial_unit"],
        row["species"],
        row["product"],
        row["licence"],
        row["perspective"],
        row["component"],
    )


def merge_blocks(blocks: Sequence[LedgerBlock]) -> tuple[dict[str, str], ...]:
    """Concatenate blocks in order and fail on duplicate or split sources."""
    if not blocks:
        raise ValueError("merge requires at least one ledger block")

    merged: list[dict[str, str]] = []
    seen_keys: dict[tuple[str, ...], str] = {}
    source_jurisdictions: dict[str, set[str]] = defaultdict(set)

    for block in blocks:
        for row in block.rows:
            key = ledger_key(row)
            if key in seen_keys:
                raise LedgerValidationError(
                    "duplicate 9-field key across blocks: "
                    f"{'/'.join(key)} (first in {seen_keys[key]}, again in "
                    f"{block.path})"
                )
            seen_keys[key] = str(block.path)
            source_jurisdictions[row["source"]].add(row["jurisdiction"])
            merged.append(row)

    split_sources = {
        source: jurisdictions
        for source, jurisdictions in source_jurisdictions.items()
        if len(jurisdictions) > 1
    }
    if split_sources:
        raise LedgerValidationError(
            f"a source must not straddle jurisdictions: {split_sources}"
        )
    return tuple(merged)


def write_ledger(path: Path, rows: Iterable[Mapping[str, str]]) -> Ledger:
    """Write atomically and validate the merged file before publishing it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row[name] for name in LEDGER_FIELDS})
        ledger = load_ledger(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return ledger


def _share(numerator: float, denominator: float, *, digits: int = 6) -> dict[str, object]:
    if denominator <= 0:
        raise ValueError(f"denominator must be > 0, got {denominator}")
    return {
        "numerator": round(numerator, 6),
        "denominator": round(denominator, 6),
        "share": round(numerator / denominator, digits),
    }


def _bcts_unique_sales(ledger: Ledger) -> dict[str, float]:
    """One weight per awarded sale; never sum a row weight across components."""
    volumes: dict[str, float] = {}
    for row in ledger:
        if row.source != "prov_bcts_auction_results":
            continue
        previous = volumes.get(row.licence.value)
        if previous is None:
            volumes[row.licence.value] = row.volume_m3
        elif abs(previous - row.volume_m3) > 1e-9:
            raise LedgerValidationError(
                f"BCTS sale {row.licence.value} has inconsistent volumes "
                f"{previous} and {row.volume_m3}"
            )
    if not volumes:
        raise LedgerValidationError("no BCTS sale rows in the merged ledger")
    return volumes


def _group_component_counts(ledger: Ledger) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str, str], list] = defaultdict(list)
    for row in ledger:
        groups[
            (
                row.jurisdiction.value,
                row.source,
                row.perspective.value,
                row.component.value,
            )
        ].append(row)

    counts: list[dict[str, object]] = []
    for key in sorted(
        groups,
        key=lambda item: (
            item[0],
            item[1],
            item[2],
            COMPONENT_ORDER[item[3]],
        ),
    ):
        rows = groups[key]
        units = sorted({row.unit.value for row in rows})
        if len(units) > 1:
            raise LedgerValidationError(
                f"component {key!r} mixes units {units}; a coverage group must "
                "have one rate denominator"
            )
        quantity_unit = Unit(units[0]).quantity_unit.value
        counts.append(
            {
                "jurisdiction": key[0],
                "source": key[1],
                "perspective": key[2],
                "component": key[3],
                "rows": len(rows),
                "licences": len({row.licence.value for row in rows}),
                "years": sorted({row.year for row in rows}),
                "units": sorted({row.unit.value for row in rows}),
                "quantity_unit": quantity_unit,
                "quantity_weight": round(sum(row.volume_m3 for row in rows), 6),
                "confidence": sorted({row.confidence.value for row in rows}),
                "quantity_note": (
                    "sale weight, not harvest"
                    if key[0] == "BC"
                    else "rate denominator, not observed volume"
                ),
            }
        )
    return counts


def _value_totals(ledger: Ledger) -> list[dict[str, object]]:
    """Totals within one perspective, currency/year, and unit only."""
    groups: dict[tuple[str, str, int, str, str, str], list] = defaultdict(list)
    for row in ledger:
        if row.is_comparison_only:
            continue
        groups[
            (
                row.jurisdiction.value,
                row.source,
                row.year,
                row.notes_tokens["currency_basis"],
                row.perspective.value,
                row.unit.value,
            )
        ].append(row)

    totals: list[dict[str, object]] = []
    for key in sorted(groups):
        rows = groups[key]
        totals.append(
            {
                "jurisdiction": key[0],
                "source": key[1],
                "year": key[2],
                "currency_basis": key[3],
                "perspective": key[4],
                "unit": key[5],
                "rows": len(rows),
                "value_cad": round(
                    sum(row.value_cad * row.volume_m3 * int(row.sign) for row in rows),
                    6,
                ),
                "note": (
                    "sum(value_cad * quantity * sign) within one perspective, "
                    "year, currency basis, and unit; never added across any of "
                    "them or across provinces"
                ),
            }
        )
    return totals


def _component_readiness(
    ledger: Ledger, jurisdiction: str
) -> tuple[list[dict[str, object]], list[str]]:
    rows = [
        row
        for row in ledger
        if row.jurisdiction.value == jurisdiction
        and row.perspective is Perspective.FOREST_SECTOR
    ]
    by_component: dict[Component, list] = defaultdict(list)
    for row in rows:
        by_component[row.component].append(row)

    readiness: list[dict[str, object]] = []
    unusable: list[str] = []
    for component in NET_VALUE_COMPONENTS:
        component_rows = by_component.get(component, [])
        if not component_rows:
            readiness.append(
                {
                    "component": component.value,
                    "status": "missing",
                    "rows": 0,
                    "reasons": ["no row in the merged sample"],
                }
            )
            unusable.append(component.value)
            continue

        reasons: list[str] = []
        status = "present"
        if any(row.confidence is Confidence.LOW for row in component_rows):
            status = "partial"
            reasons.append("at least one row is low confidence")
        if any(row.notes_tokens.get("not_for_net_value") == "1" for row in component_rows):
            status = "partial"
            reasons.append("source marks the row not_for_net_value=1")
        if any("coverage_completeness=partial" in row.notes for row in component_rows):
            status = "partial"
            reasons.append("source declares partial completeness")
        if any(row.unit is Unit.CAD_PER_HA for row in component_rows):
            status = "partial"
            reasons.append("CAD_per_ha requires a yield conversion before net value")
        if any(
            "tertiary" in row.notes or "tertiaire" in row.notes
            for row in component_rows
        ):
            status = "partial"
            reasons.append("tertiary road cost is excluded from the per-m3 rate")
        if component is Component.STUMPAGE_BASE and jurisdiction == "BC":
            base_licences = {row.licence.value for row in component_rows}
            premium_licences = {
                row.licence.value
                for row in rows
                if row.component is Component.STUMPAGE_BID_PREMIUM
            }
            if len(base_licences) < len(premium_licences):
                status = "partial"
                reasons.append(
                    f"upset base observed for {len(base_licences)} of "
                    f"{len(premium_licences)} awarded sales; missing is not zero"
                )

        readiness.append(
            {
                "component": component.value,
                "status": status,
                "rows": len(component_rows),
                "years": sorted({row.year for row in component_rows}),
                "units": sorted({row.unit.value for row in component_rows}),
                "confidence": sorted(
                    {row.confidence.value for row in component_rows}
                ),
                "reasons": reasons,
            }
        )
        if status != "present":
            unusable.append(component.value)
    return readiness, unusable


def _load_json(path: Path) -> Mapping[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def build_coverage_summary(
    ledger: Ledger,
    *,
    blocks: Sequence[LedgerBlock],
    output_path: Path,
) -> dict[str, object]:
    bcts_sales = _bcts_unique_sales(ledger)
    bcts_volume = sum(bcts_sales.values())
    bcts_schedule = _load_json(BCTS_SUMMARY_PATH)
    bcts_results = _load_json(BCTS_RESULTS_PROVENANCE_PATH)

    schedule_counts = bcts_schedule["counts"]
    schedule_volume = float(bcts_schedule["volume_m3"]["total"])
    schedule_tsls = int(schedule_counts["unique_licences"])
    schedule_blocks = int(schedule_counts["unique_licence_block"])
    awarded_tsls = len(bcts_sales)
    awarded_blocks = int(bcts_results["counts"]["awarded_block_count"])

    qc_rows = [
        row for row in ledger if row.jurisdiction.value == "QC"
    ]
    qc_current_stumpage = [
        row
        for row in qc_rows
        if row.year == 2025
        and row.component is Component.STUMPAGE_BASE
        and row.perspective is Perspective.CROWN
    ]
    qc_stumpage_years = sorted(
        {
            row.year
            for row in qc_rows
            if row.component is Component.STUMPAGE_BASE
        }
    )
    qc_cost_components = sorted(
        {
            row.component.value
            for row in qc_rows
            if row.perspective is Perspective.FOREST_SECTOR
            and row.component
            in {
                Component.HAUL,
                Component.ROAD,
                Component.OVERHEAD,
                Component.HARVEST,
                Component.SILVICULTURE,
            }
        }
    )

    bc_readiness, bc_unusable = _component_readiness(ledger, "BC")
    qc_readiness, qc_unusable = _component_readiness(ledger, "QC")

    summary: dict[str, object] = {
        "artifact": "forest_cost_revenue_merged",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "screening_only",
        "policy_grade": False,
        "partial_coverage": True,
        "sample_not_population": True,
        "net_value_status": "not_estimated",
        "output": {
            "path": _display_path(output_path),
            "rows": len(ledger),
            "sha256": sha256_file(output_path),
        },
        "inputs": [
            {
                "path": _display_path(block.path),
                "jurisdiction": block.jurisdiction,
                "rows": len(block),
                "sha256": block.sha256,
            }
            for block in blocks
        ],
        "merge_rules": {
            "province_partitioned": True,
            "sources_not_added": True,
            "perspectives_not_added": True,
            "mirrors_preserved": True,
            "no_imputation": True,
            "no_delivered_log_revenue_unless_observed": True,
        },
        "row_counts": {
            "total_rows": len(ledger),
            "by_jurisdiction": _count(ledger, lambda row: row.jurisdiction.value),
            "by_source": _count(ledger, lambda row: row.source),
            "by_perspective": _count(ledger, lambda row: row.perspective.value),
            "by_probable_origin": _count(
                ledger,
                lambda row: f"{row.jurisdiction.value}/{row.source}",
            ),
            "by_component": _count(ledger, lambda row: row.component.value),
            "by_jurisdiction_source_perspective_component": _group_component_counts(
                ledger
            ),
        },
        "mirrors": {
            "mirror_rows": sum(1 for row in ledger if row.mirror_requested),
            "transfer_pairs": sum(1 for row in ledger if row.mirror_requested),
            "unpaired": 0,
            "note": "load_ledger validated every mirror pair; all are Crown/sector transfers",
        },
        "value_totals_cad": _value_totals(ledger),
        "coverage": {
            "denominators": {
                "national_harvest_2024_m3": NATIONAL_HARVEST_2024_M3,
                "bc_harvest_2024_m3": BC_HARVEST_2024_M3,
                "qc_harvest_2024_m3": QC_HARVEST_2024_M3,
                "bc_plus_qc_share_of_national": _share(
                    BC_HARVEST_2024_M3 + QC_HARVEST_2024_M3,
                    NATIONAL_HARVEST_2024_M3,
                ),
                "source": "data/derived/national/harvest_basis/harvest_basis_template.csv (2024 actual_harvest_m3)",
                "rounded_as_in_brief": {
                    "national": "118.98M m3",
                    "bc": "38.88M m3",
                    "qc": "25.94M m3",
                },
            },
            "bc_bcts": {
                "sample_frame": {
                    "scheduled_tsls": schedule_tsls,
                    "scheduled_blocks": schedule_blocks,
                    "scheduled_volume_m3": round(schedule_volume, 6),
                    "source": "bcts_sales_summary.json (BCTS Sales Schedule public extract)",
                },
                "ledger": {
                    "awarded_tsls": awarded_tsls,
                    "awarded_blocks": awarded_blocks,
                    "unique_sale_volume_m3": round(bcts_volume, 6),
                    "rows": sum(
                        1
                        for row in ledger
                        if row.source == "prov_bcts_auction_results"
                    ),
                    "sales_with_base": sum(
                        1
                        for row in ledger
                        if row.source == "prov_bcts_auction_results"
                        and row.component is Component.STUMPAGE_BASE
                        and row.perspective is Perspective.CROWN
                    ),
                    "sales_with_premium": sum(
                        1
                        for row in ledger
                        if row.source == "prov_bcts_auction_results"
                        and row.component is Component.STUMPAGE_BID_PREMIUM
                        and row.perspective is Perspective.CROWN
                    ),
                },
                "awarded_share_of_scheduled_volume": _share(
                    bcts_volume, schedule_volume
                ),
                "awarded_tsl_share_of_scheduled": _share(
                    awarded_tsls, schedule_tsls
                ),
                "awarded_block_share_of_scheduled": _share(
                    awarded_blocks, schedule_blocks
                ),
                "awarded_volume_share_of_bc_2024_harvest": _share(
                    bcts_volume, BC_HARVEST_2024_M3
                ),
                "scheduled_volume_share_of_bc_2024_harvest": _share(
                    schedule_volume, BC_HARVEST_2024_M3
                ),
                "sample_not_population": True,
            },
            "qc_meris": {
                "sample_frame": {
                    "tarification_zones": QC_TARIFICATION_ZONES,
                    "stumpage_rate_rows": QC_STUMPAGE_RATE_ROWS,
                    "treatments": QC_TREATMENT_ROWS,
                    "sample_zone": "QC-ZONE_151",
                    "source": "MERIS 3.3.0 tables_key.csv and README.md",
                },
                "ledger": {
                    "rows": len(qc_rows),
                    "current_stumpage_rate_rows": len(qc_current_stumpage),
                    "stumpage_years": qc_stumpage_years,
                    "cost_components": qc_cost_components,
                    "observed_volume_m3": 0.0,
                },
                "current_rate_row_share": _share(
                    len(qc_current_stumpage), QC_STUMPAGE_RATE_ROWS
                ),
                "zone_share": _share(1, QC_TARIFICATION_ZONES),
                "treatment_share": _share(1, QC_TREATMENT_ROWS),
                "harvest_volume_share": {
                    "numerator": 0.0,
                    "denominator": QC_HARVEST_2024_M3,
                    "share": None,
                    "status": "not_estimable",
                    "reason": "MERIS has no observed permit-volume register; ledger rows use volume_m3=1.0 as a rate denominator",
                },
                "sample_not_population": True,
            },
        },
        "net_value_readiness": {
            "perspective": "forest_sector",
            "bc": {
                "source": "prov_bcts_auction_results",
                "components": bc_readiness,
                "missing_or_partial": sorted(set(bc_unusable)),
                "net_value_status": "not_estimated",
                "reason": "delivered log revenue and every production cost are absent; a standing bid is not a log price",
            },
            "qc": {
                "source": "prov_qc_meris",
                "components": qc_readiness,
                "missing_or_partial": sorted(set(qc_unusable)),
                "net_value_status": "not_estimated",
                "reason": "delivered log revenue is absent, harvest is loading-only, and silviculture is CAD/ha without a yield conversion",
            },
        },
        "caveats": [
            "The merged file is a sample ledger, not a provincial or national population estimate.",
            "BCTS and MERIS rows are never summed; each remains a separate source under its own province.",
            "Crown and forest-sector rows are never summed into a combined total; only the reconciliation identity may relate them.",
            "BCTS sells standing timber: its upset and bonus are stumpage, not delivered log revenue.",
            "MERIS is a tarification/cost parameter set, not an observed harvest or delivered-price register.",
            "Missing components are missing, not zero; no value, volume, or component was imputed.",
        ],
    }
    return summary


def _count(ledger: Ledger, key_fn) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in ledger:
        counts[key_fn(row)] += 1
    return dict(sorted(counts.items()))


def _percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _status_badge(status: str) -> str:
    return {"present": "present", "partial": "partial", "missing": "**missing**"}[status]


def render_markdown(summary: Mapping[str, object]) -> str:
    counts = summary["row_counts"]
    coverage = summary["coverage"]
    bc = coverage["bc_bcts"]
    qc = coverage["qc_meris"]
    denominators = coverage["denominators"]
    lines: list[str] = []
    lines.append("# Forest cost/revenue ledger coverage")
    lines.append("")
    lines.append(
        f"**Status:** `{summary['status']}` · `policy_grade=false` · "
        "**sample, not a population**"
    )
    lines.append("")
    lines.append(f"**Generated:** {summary['generated_utc']}")
    lines.append("")
    lines.append(
        f"**Ledger:** `{summary['output']['path']}` "
        f"({summary['output']['rows']} rows; SHA-256 "
        f"`{summary['output']['sha256'][:12]}…`)"
    )
    lines.append("")
    lines.append("## 1. Merge")
    lines.append("")
    lines.append("The two blocks are concatenated, never re-keyed and never summed:")
    lines.append("")
    lines.append("| Province | Source | Rows | SHA-256 |")
    lines.append("|---|---|---:|---|")
    for item in summary["inputs"]:
        lines.append(
            f"| {item['jurisdiction']} | `{item['path']}` | {item['rows']} | "
            f"`{item['sha256'][:12]}…` |"
        )
    lines.append("")
    lines.append(
        f"Merged rows: **{counts['total_rows']}**. By province: "
        + ", ".join(f"{key} {value}" for key, value in counts["by_jurisdiction"].items())
        + ". By perspective: "
        + ", ".join(
            f"{key} {value}" for key, value in counts["by_perspective"].items()
        )
        + "."
    )
    lines.append("")
    lines.append("### Rows by province / source / perspective / component")
    lines.append("")
    lines.append(
        "| Province | Source | Perspective | Component | Rows | Licences | "
        "Unit | Quantity weight | Confidence |"
    )
    lines.append("|---|---|---|---|---:|---:|---|---:|---|")
    for entry in counts["by_jurisdiction_source_perspective_component"]:
        lines.append(
            f"| {entry['jurisdiction']} | `{entry['source']}` | "
            f"{entry['perspective']} | `{entry['component']}` | {entry['rows']} | "
            f"{entry['licences']} | {', '.join(entry['units'])} | "
            f"{entry['quantity_weight']:,.2f} | "
            f"{', '.join(entry['confidence'])} |"
        )
    lines.append("")
    lines.append(
        "Quantity weight is the sale volume for BCTS and a 1 m³ rate "
        "denominator for MERIS; it is not summed across components or sources."
    )
    lines.append("")
    lines.append("## 2. Coverage against population denominators")
    lines.append("")
    lines.append(
        "**Not a population.** The ledger covers selected BCTS auctions and "
        "one MERIS tarification zone. The shares below describe how much of "
        "each source frame is represented; they do **not** estimate provincial "
        "values or allow scaling to the provincial harvest."
    )
    lines.append("")
    lines.append("| Measure | Numerator | Denominator | Share |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| BCTS scheduled sample volume | "
        f"{bc['sample_frame']['scheduled_volume_m3']:,.0f} m³ | "
        f"BC 2024 harvest {denominators['bc_harvest_2024_m3']:,.0f} m³ | "
        f"{_percent(bc['scheduled_volume_share_of_bc_2024_harvest']['share'])} |"
    )
    lines.append(
        f"| BCTS awarded ledger volume | "
        f"{bc['ledger']['unique_sale_volume_m3']:,.0f} m³ | "
        f"BC 2024 harvest {denominators['bc_harvest_2024_m3']:,.0f} m³ | "
        f"{_percent(bc['awarded_volume_share_of_bc_2024_harvest']['share'])} |"
    )
    lines.append(
        f"| BCTS awarded volume | "
        f"{bc['ledger']['unique_sale_volume_m3']:,.0f} m³ | "
        f"BCTS scheduled sample {bc['sample_frame']['scheduled_volume_m3']:,.0f} m³ | "
        f"{_percent(bc['awarded_share_of_scheduled_volume']['share'])} |"
    )
    lines.append(
        f"| BCTS awarded TSLs | {bc['ledger']['awarded_tsls']} | "
        f"{bc['sample_frame']['scheduled_tsls']} scheduled TSLs | "
        f"{_percent(bc['awarded_tsl_share_of_scheduled']['share'])} |"
    )
    lines.append(
        f"| BCTS awarded blocks | {bc['ledger']['awarded_blocks']} | "
        f"{bc['sample_frame']['scheduled_blocks']} scheduled blocks | "
        f"{_percent(bc['awarded_block_share_of_scheduled']['share'])} |"
    )
    lines.append(
        f"| MERIS current stumpage rate rows | "
        f"{qc['ledger']['current_stumpage_rate_rows']} | "
        f"{qc['sample_frame']['stumpage_rate_rows']:,} zone-species rows | "
        f"{_percent(qc['current_rate_row_share']['share'])} |"
    )
    lines.append(
        f"| MERIS tarification zones | 1 (zone 151) | "
        f"{qc['sample_frame']['tarification_zones']} zones | "
        f"{_percent(qc['zone_share']['share'])} |"
    )
    lines.append(
        f"| MERIS silviculture treatments | 1 (BPR) | "
        f"{qc['sample_frame']['treatments']} treatments | "
        f"{_percent(qc['treatment_share']['share'])} |"
    )
    lines.append(
        f"| MERIS observed harvest volume | 0 | "
        f"QC 2024 harvest {denominators['qc_harvest_2024_m3']:,.0f} m³ | "
        "**not estimable** |"
    )
    lines.append("")
    lines.append(
        f"For context only: BC + QC are "
        f"{_percent(denominators['bc_plus_qc_share_of_national']['share'])} of "
        f"the 2024 national harvest denominator "
        f"({denominators['national_harvest_2024_m3']:,.0f} m³). This is a "
        "denominator share, not ledger coverage, and BC + QC values are not "
        "combined."
    )
    lines.append("")
    lines.append("### BCTS sample detail")
    lines.append("")
    lines.append(
        f"- Schedule frame: {bc['sample_frame']['scheduled_tsls']} TSLs, "
        f"{bc['sample_frame']['scheduled_blocks']} blocks, "
        f"{bc['sample_frame']['scheduled_volume_m3']:,.0f} m³."
    )
    lines.append(
        f"- Awarded ledger: {bc['ledger']['awarded_tsls']} TSLs, "
        f"{bc['ledger']['awarded_blocks']} blocks, "
        f"{bc['ledger']['unique_sale_volume_m3']:,.0f} m³ unique sale weight."
    )
    lines.append(
        f"- Upset base is present for {bc['ledger']['sales_with_base']} sales; "
        f"the bonus premium is present for {bc['ledger']['sales_with_premium']}. "
        "Two lump-sum sales publish a bonus but no upset; no base was fabricated."
    )
    lines.append("")
    lines.append("### QC MERIS sample detail")
    lines.append("")
    lines.append(
        f"- One tarification zone (151), {qc['ledger']['current_stumpage_rate_rows']} "
        f"current zone-species stumpage rows, cost components "
        f"{', '.join(qc['ledger']['cost_components'])}."
    )
    lines.append(
        "- `harvest` is loading-only (`not_for_net_value=1`), `road` excludes "
        "tertiary road, and `silviculture` is a CAD/ha treatment sample."
    )
    lines.append(
        "- MERIS has no observed permit-volume register: every row uses "
        "`volume_m3=1.0` as a rate denominator, so QC harvest-volume coverage "
        "is not estimable."
    )
    lines.append("")
    lines.append("## 3. Net-value readiness")
    lines.append("")
    lines.append(
        "Net value is reported only in the `forest_sector` perspective and is "
        "`not_estimated` while any in-boundary component is missing or partial. "
        "`stumpage_total` is an alternative to `stumpage_base` plus "
        "`stumpage_bid_premium`, not an additional requirement."
    )
    lines.append("")
    lines.append("| Component | BC BCTS | QC MERIS |")
    lines.append("|---|---|---|")
    bc_components = {
        entry["component"]: entry
        for entry in summary["net_value_readiness"]["bc"]["components"]
    }
    qc_components = {
        entry["component"]: entry
        for entry in summary["net_value_readiness"]["qc"]["components"]
    }
    for component in NET_VALUE_COMPONENTS:
        bc_entry = bc_components[component.value]
        qc_entry = qc_components[component.value]
        lines.append(
            f"| `{component.value}` | {_status_badge(bc_entry['status'])} | "
            f"{_status_badge(qc_entry['status'])} |"
        )
    lines.append("")
    lines.append(
        f"**BC net value:** `{summary['net_value_readiness']['bc']['net_value_status']}` — "
        f"{summary['net_value_readiness']['bc']['reason']}."
    )
    lines.append("")
    lines.append(
        f"**QC net value:** `{summary['net_value_readiness']['qc']['net_value_status']}` — "
        f"{summary['net_value_readiness']['qc']['reason']}."
    )
    lines.append("")
    lines.append("Present/partial components:")
    lines.append("")
    for jurisdiction, key in (("BC", "bc"), ("QC", "qc")):
        present = [
            entry["component"]
            for entry in summary["net_value_readiness"][key]["components"]
            if entry["status"] != "missing"
        ]
        lines.append(
            f"- {jurisdiction}: "
            + (", ".join(f"`{name}`" for name in present) if present else "none")
            + "."
        )
    lines.append("")
    lines.append("## 4. Validation")
    lines.append("")
    lines.append(
        f"- `load_ledger` accepted all {counts['total_rows']} rows after the merge."
    )
    lines.append(
        f"- {summary['mirrors']['mirror_rows']} mirror rows are paired exactly; "
        "all are Crown/forest-sector transfers."
    )
    lines.append(
        "- Duplicate keys, source-exclusivity conflicts, sign violations, "
        "currency/volume-basis mixing, and unpaired mirrors fail the merge."
    )
    lines.append(
        "- No `delivered_log_revenue` row exists in either source; none was "
        "invented."
    )
    lines.append("")
    lines.append("## 5. Caveats")
    lines.append("")
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    lines.append("## 6. Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append(
        ".venv/bin/python -m workflows.national.harvest.merge_cost_revenue"
    )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def run_merge(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    block_specs: Sequence[tuple[Path, str]] = BLOCK_SPECS,
) -> MergeResult:
    """Read, merge, validate, and write the ledger and coverage artifacts."""
    blocks = tuple(
        read_ledger_block(path, jurisdiction) for path, jurisdiction in block_specs
    )
    merged_rows = merge_blocks(blocks)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / DEFAULT_OUTPUT_CSV.name
    ledger = write_ledger(output_path, merged_rows)

    summary = build_coverage_summary(
        ledger,
        blocks=blocks,
        output_path=output_path,
    )
    coverage_json_path = output_dir / DEFAULT_COVERAGE_JSON.name
    coverage_md_path = output_dir / DEFAULT_COVERAGE_MD.name
    coverage_json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    coverage_md_path.write_text(render_markdown(summary), encoding="utf-8")

    return MergeResult(
        ledger=ledger,
        output_path=output_path,
        coverage_json_path=coverage_json_path,
        coverage_md_path=coverage_md_path,
        summary=summary,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directory for the merged ledger and coverage artifacts",
    )
    args = parser.parse_args(argv)
    result = run_merge(output_dir=args.output_dir)
    print(
        f"merged {len(result.ledger)} rows -> {result.output_path}; "
        f"coverage -> {result.coverage_json_path.name}, "
        f"{result.coverage_md_path.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
