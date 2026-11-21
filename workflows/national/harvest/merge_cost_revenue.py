"""Merge the BC BCTS and full QC MERIS cost/revenue blocks and report coverage.

The two inputs are already in the canonical ledger schema: the original 16
core fields plus optional ``quality`` and ``dbh_cm`` columns.  This module
concatenates them without re-keying, re-weighting, imputing, or cross-province
aggregation.  Each block is validated on its own before the
merge and the merged file is validated again with
:func:`workflows.national.harvest.cost_revenue.load_ledger`.

The QC input is the full MERIS model-input deck (2,688 rows: 251
quality-class price rows, 1,824 species/DBH curve rows, 191 road, 191
overhead, 191 loading-only harvest, and 40 silviculture rows).  The earlier 17-row
sample is deliberately excluded: its keys overlap the full block, so merging
both would double-count source keys.  Observed BCTS rows and model-input MERIS
rows are counted and reported separately, and model-input values are never
monetised.

Outputs under ``data/derived/national/harvest_basis/forest_cost_revenue/``:

* ``forest_cost_revenue.csv`` -- the merged audit ledger;
* ``coverage_summary.json`` -- machine-readable row counts and coverage math;
* ``FOREST_COST_REVENUE_COVERAGE.md`` -- the human-readable coverage report.

The coverage report is deliberately explicit that the BCTS block is a selected
sample and the MERIS block is a model-input deck.  It never scales BCTS to BC,
MERIS to QC, or BC/QC to Canada, and it never adds the Crown and forest-sector
perspectives together.
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
    CORE_LEDGER_FIELDS,
    LEDGER_FIELDS,
    Component,
    Confidence,
    Evidence,
    Ledger,
    LedgerValidationError,
    Perspective,
    Quality,
    Unit,
    load_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"

BC_BLOCK_PATH = HARVEST_BASIS_DIR / "bcts_bc/bcts_bid_ledger.csv"
QC_BLOCK_PATH = HARVEST_BASIS_DIR / "meris_qc_full/forest_cost_revenue_qc_model.csv"
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

# MERIS full-deck counts, from meris_qc_full/manifest.json and
# QC_MERIS_DECK_REPORT.md.  The full deck replaces the earlier 17-row sample;
# the sample must not be merged again because its keys overlap the full block.
QC_TARIFICATION_ZONES = 191
QC_STUMPAGE_RATE_ROWS = 2_674
QC_TREATMENT_ROWS = 94
QC_TREATMENTS_USABLE = 40
QC_PRICE_DECK_CELLS = 630
QC_DBH_SPECIES = 12
QC_DBH_CLASSES = 76
QC_DBH_SERIES = 2
QC_DBH_PROFILE_CELLS = QC_DBH_SPECIES * QC_DBH_CLASSES * QC_DBH_SERIES
QC_CURVE_PROFILE = "Profil_2"
OBSERVED_EVIDENCE = frozenset(
    {
        Evidence.OBSERVED_PAYMENT,
        Evidence.OBSERVED_SALE,
        Evidence.OBSERVED_PRICE,
    }
)
MODEL_EVIDENCE = frozenset({Evidence.MODEL_INPUT, Evidence.MODEL_DERIVED})

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
        missing = [name for name in CORE_LEDGER_FIELDS if name not in header]
        unknown = [name for name in header if name not in LEDGER_FIELDS]
        if missing or unknown:
            raise LedgerValidationError(
                f"{path} header {header!r} does not match the canonical schema "
                f"{list(LEDGER_FIELDS)!r} (missing={missing}, unknown={unknown}); "
                "quality and dbh_cm are optional for legacy 16-field blocks"
            )
        rows: list[dict[str, str]] = []
        for line_number, record in enumerate(reader, start=2):
            extra = record.get(None)
            if extra is not None:
                raise LedgerValidationError(
                    f"{path} line {line_number}: row has {len(extra)} value(s) "
                    f"beyond the {len(header)} columns"
                )
            row = {
                name: (record.get(name) or "").strip() for name in LEDGER_FIELDS
            }
            if not any(row.values()):
                continue
            row["quality"] = row["quality"] or Quality.NA.value
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
        row["quality"],
        row["dbh_cm"],
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
        row["quality"],
        row["dbh_cm"],
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
                    "duplicate ledger key across blocks: "
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
            writer = csv.DictWriter(
                handle, fieldnames=LEDGER_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in LEDGER_FIELDS})
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


def _is_model_input(row) -> bool:
    """A model-input row is explicit, never inferred from a missing price."""
    return row.value_basis.evidence is Evidence.MODEL_INPUT


def _is_observed(row) -> bool:
    return row.value_basis.evidence in OBSERVED_EVIDENCE


def _evidence_class_counts(ledger: Ledger) -> dict[str, object]:
    by_jurisdiction: dict[str, dict[str, int]] = defaultdict(
        lambda: {"observed": 0, "model_input": 0, "other": 0}
    )
    for row in ledger:
        evidence_class = (
            "model_input" if _is_model_input(row) else "observed" if _is_observed(row) else "other"
        )
        by_jurisdiction[row.jurisdiction.value][evidence_class] += 1
    return {
        "observed_rows": sum(1 for row in ledger if _is_observed(row)),
        "model_input_rows": sum(1 for row in ledger if _is_model_input(row)),
        "other_rows": sum(
            1 for row in ledger if not _is_observed(row) and not _is_model_input(row)
        ),
        "by_jurisdiction": dict(sorted(by_jurisdiction.items())),
        "rule": (
            "observed = observed_payment/observed_sale/observed_price; "
            "model_input = model_input evidence with model_input=1 and "
            "not_observed=1; the classes are counted separately and never summed"
        ),
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
                "evidence": sorted(
                    {
                        "model_input"
                        if _is_model_input(row)
                        else "observed" if _is_observed(row) else "other"
                        for row in rows
                    }
                ),
                "quantity_note": (
                    "sale weight, not harvest"
                    if key[0] == "BC"
                    else "rate denominator, not observed volume"
                ),
            }
        )
    return counts


def _value_totals(ledger: Ledger) -> list[dict[str, object]]:
    """Totals only within one source, perspective, component, year, and unit.

    Model-input rows are counted but never monetised: a MERIS rate deck is not
    a transaction and summing its per-species or per-zone rates would create a
    meaningless monetary total.
    """
    groups: dict[tuple[str, str, int, str, str, str, str, str], list] = defaultdict(list)
    for row in ledger:
        if row.is_comparison_only:
            continue
        evidence_class = (
            "model_input" if _is_model_input(row) else "observed" if _is_observed(row) else "other"
        )
        groups[
            (
                row.jurisdiction.value,
                row.source,
                row.year,
                row.notes_tokens["currency_basis"],
                row.perspective.value,
                row.component.value,
                row.unit.value,
                evidence_class,
            )
        ].append(row)

    totals: list[dict[str, object]] = []
    for key in sorted(groups):
        rows = groups[key]
        evidence_class = key[7]
        totals.append(
            {
                "jurisdiction": key[0],
                "source": key[1],
                "year": key[2],
                "currency_basis": key[3],
                "perspective": key[4],
                "component": key[5],
                "unit": key[6],
                "evidence_class": evidence_class,
                "rows": len(rows),
                "value_cad": (
                    None
                    if evidence_class == "model_input"
                    else round(
                        sum(
                            row.value_cad * row.volume_m3 * int(row.sign)
                            for row in rows
                        ),
                        6,
                    )
                ),
                "status": (
                    "not_summed_model_input"
                    if evidence_class == "model_input"
                    else "within_source_perspective_component_total"
                ),
                "note": (
                    "only observed rows are monetised, and only within one "
                    "source, perspective, component, year, currency basis, and "
                    "unit; never added across any of them or across provinces"
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
        if any(_is_model_input(row) for row in component_rows):
            status = "partial"
            reasons.append("model-input deck, not an observed transaction")
        if any(row.notes_tokens.get("not_observed") == "1" for row in component_rows):
            status = "partial"
            reasons.append("source marks the row not_observed=1")
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
    qc_delivered_rows = [
        row for row in qc_rows if row.component is Component.DELIVERED_LOG_REVENUE
    ]
    qc_observed_delivered_rows = [
        row for row in qc_delivered_rows if _is_observed(row)
    ]
    qc_model_input_delivered_rows = [
        row for row in qc_delivered_rows if _is_model_input(row)
    ]
    qc_quality_delivered_rows = [
        row
        for row in qc_model_input_delivered_rows
        if row.quality is not Quality.NA
    ]
    qc_dbh_delivered_rows = [
        row
        for row in qc_model_input_delivered_rows
        if row.dbh_cm is not None
    ]
    qc_price_deck_delivered_rows = [
        row
        for row in qc_model_input_delivered_rows
        if row.dbh_cm is None
    ]
    qc_quality_counts: dict[str, int] = defaultdict(int)
    for row in qc_quality_delivered_rows:
        qc_quality_counts[row.quality.value] += 1
    qc_series_counts: dict[str, int] = defaultdict(int)
    for row in qc_delivered_rows:
        series = row.notes_tokens.get("series")
        if series is not None:
            qc_series_counts[series] += 1
    qc_dbh_species = sorted(
        {row.species for row in qc_dbh_delivered_rows}
    )
    qc_dbh_values = sorted(
        {float(row.dbh_cm) for row in qc_dbh_delivered_rows if row.dbh_cm}
    )
    if len(qc_dbh_delivered_rows) != QC_DBH_PROFILE_CELLS:
        raise LedgerValidationError(
            "QC DBH curve rows do not match the single-profile deck size: "
            f"{len(qc_dbh_delivered_rows)} != {QC_DBH_PROFILE_CELLS}"
        )
    if len(qc_dbh_species) != QC_DBH_SPECIES:
        raise LedgerValidationError(
            f"QC DBH curve species count {len(qc_dbh_species)} != "
            f"{QC_DBH_SPECIES}"
        )
    if len(qc_dbh_values) != QC_DBH_CLASSES:
        raise LedgerValidationError(
            f"QC DBH class count {len(qc_dbh_values)} != {QC_DBH_CLASSES}"
        )
    qc_zone_ids = {
        row.spatial_unit
        for row in qc_rows
        if row.spatial_unit.startswith("QC-ZONE_")
    }
    qc_treatments_covered = len(
        {
            row.notes_tokens.get("treatment")
            for row in qc_rows
            if row.component is Component.SILVICULTURE
            and row.notes_tokens.get("treatment")
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
            "no_observed_delivered_log_revenue_injected": True,
            "observed_and_model_input_rows_separated": True,
            "model_input_not_monetised": True,
            "quality_and_dbh_preserved": True,
            "price_series_not_summed": True,
            "dbh_profiles_deduplicated": True,
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
            "by_evidence_class": _count(
                ledger,
                lambda row: (
                    "model_input"
                    if _is_model_input(row)
                    else "observed" if _is_observed(row) else "other"
                ),
            ),
            "by_jurisdiction_source_perspective_component": _group_component_counts(
                ledger
            ),
        },
        "evidence_classes": _evidence_class_counts(ledger),
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
                    "treatments_usable": QC_TREATMENTS_USABLE,
                    "price_deck_cells": QC_PRICE_DECK_CELLS,
                    "dbh_curve_cells": QC_DBH_PROFILE_CELLS,
                    "dbh_curve_species": QC_DBH_SPECIES,
                    "dbh_curve_classes": QC_DBH_CLASSES,
                    "dbh_curve_series": QC_DBH_SERIES,
                    "dbh_curve_profile": QC_CURVE_PROFILE,
                    "deck": "full_model_input",
                    "source": "meris_qc_full/manifest.json and QC_MERIS_DECK_REPORT.md (MERIS 3.3.0, database release 2025-09-23)",
                },
                "ledger": {
                    "rows": len(qc_rows),
                    "observed_rows": sum(1 for row in qc_rows if _is_observed(row)),
                    "model_input_rows": sum(
                        1 for row in qc_rows if _is_model_input(row)
                    ),
                    "zones_covered": len(qc_zone_ids),
                    "treatments_covered": qc_treatments_covered,
                    "delivered_log_revenue_rows": len(qc_delivered_rows),
                    "delivered_log_revenue_observed_rows": len(
                        qc_observed_delivered_rows
                    ),
                    "delivered_log_revenue_model_input_rows": len(
                        qc_model_input_delivered_rows
                    ),
                    "delivered_log_revenue_quality_rows": len(
                        qc_quality_delivered_rows
                    ),
                    "delivered_log_revenue_dbh_rows": len(
                        qc_dbh_delivered_rows
                    ),
                    "delivered_log_revenue_price_deck_rows": len(
                        qc_price_deck_delivered_rows
                    ),
                    "quality_rows_by_class": dict(
                        sorted(qc_quality_counts.items())
                    ),
                    "delivered_log_revenue_series_counts": dict(
                        sorted(qc_series_counts.items())
                    ),
                    "dbh_species": qc_dbh_species,
                    "dbh_min_cm": qc_dbh_values[0],
                    "dbh_max_cm": qc_dbh_values[-1],
                    "dbh_class_count": len(qc_dbh_values),
                    "current_stumpage_rate_rows": len(qc_current_stumpage),
                    "stumpage_years": qc_stumpage_years,
                    "cost_components": qc_cost_components,
                    "observed_volume_m3": 0.0,
                    "observed_transactions": False,
                },
                "current_rate_row_share": {
                    **_share(len(qc_current_stumpage), QC_STUMPAGE_RATE_ROWS),
                    "status": "not_in_full_model_input_deck",
                },
                "zone_share": {
                    **_share(len(qc_zone_ids), QC_TARIFICATION_ZONES),
                    "status": "full_cost_deck_zone_coverage",
                },
                "treatment_share": _share(
                    qc_treatments_covered, QC_TREATMENT_ROWS
                ),
                "delivered_log_revenue": {
                    "model_input_rows": len(qc_model_input_delivered_rows),
                    "observed_rows": len(qc_observed_delivered_rows),
                    "quality_rows": len(qc_quality_delivered_rows),
                    "dbh_rows": len(qc_dbh_delivered_rows),
                    "price_deck_rows": len(qc_price_deck_delivered_rows),
                    "quality_classes": dict(sorted(qc_quality_counts.items())),
                    "series_counts": dict(sorted(qc_series_counts.items())),
                    "status": "model_input_not_observed",
                    "price_point": "mill_gate",
                    "reason": (
                        "MERIS price-deck values are model inputs net of "
                        "processing cost; they are not mill settlements"
                    ),
                    "series_rule": (
                        "courant (_2) and tendance (_1) are alternative model "
                        "scenarios, separated by licence suffix C/T; they are "
                        "not independent observations and are not summed"
                    ),
                    "dbh_rule": (
                        "species x DBH curves use Profil_2 only; the three "
                        "shipped profiles are byte-identical and are not "
                        "independent estimates"
                    ),
                },
                "harvest_volume_share": {
                    "numerator": 0.0,
                    "denominator": QC_HARVEST_2024_M3,
                    "share": None,
                    "status": "not_estimable",
                    "reason": "MERIS has no observed permit-volume register; ledger rows use volume_m3=1.0 as a rate denominator",
                },
                "sample_not_population": True,
                "observed_transactions": False,
                "model_input_only": True,
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
                "reason": "the full MERIS deck is model input only: delivered log revenue is a mill-gate model price, harvest is loading-only, road excludes tertiary road, silviculture is CAD/ha without a yield conversion, and no observed volume or full cost exists",
            },
        },
        "caveats": [
            "The ledger combines a BCTS auction sample with the full QC MERIS model-input deck; it is not a provincial or national population estimate.",
            "Observed rows and model-input rows are counted separately; model-input values are never monetised.",
            "BCTS and MERIS rows are never summed; each remains a separate source under its own province.",
            "Crown and forest-sector rows are never summed into a combined total; only the reconciliation identity may relate them.",
            "BCTS sells standing timber: its upset and bonus are stumpage, not delivered log revenue.",
            "MERIS is a tarification/cost parameter set, not an observed harvest or delivered-price register; its delivered-log values are mill-gate model inputs, not settlements.",
            "MERIS courant and tendance prices are alternative model scenarios, not independent observations; the ledger keeps both under distinct licences and never sums them.",
            "MERIS species/DBH curves are ingested from the canonical Profil_2 only; Profil_3 and Profil_4 are byte-identical in this release and must not be treated as independent estimates.",
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
        "**observed sample plus model-input deck; not a population**"
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
    evidence = summary["evidence_classes"]
    lines.append("")
    lines.append(
        f"Evidence classes: **{evidence['observed_rows']} observed** rows and "
        f"**{evidence['model_input_rows']} model-input** rows. "
        + "; ".join(
            f"{jurisdiction}: observed {classes['observed']}, "
            f"model input {classes['model_input']}"
            for jurisdiction, classes in evidence["by_jurisdiction"].items()
        )
        + ". The classes are counted separately and never summed."
    )
    lines.append("")
    lines.append("### Rows by province / source / perspective / component")
    lines.append("")
    lines.append(
        "| Province | Source | Perspective | Component | Evidence | Rows | "
        "Licences | Unit | Quantity weight | Confidence |"
    )
    lines.append("|---|---|---|---|---|---:|---:|---|---:|---|")
    for entry in counts["by_jurisdiction_source_perspective_component"]:
        lines.append(
            f"| {entry['jurisdiction']} | `{entry['source']}` | "
            f"{entry['perspective']} | `{entry['component']}` | "
            f"{', '.join(entry['evidence'])} | {entry['rows']} | "
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
        "the full MERIS model-input deck. The shares below describe how much "
        "of each source frame is represented; they do **not** estimate "
        "provincial values or allow scaling to the provincial harvest. "
        "MERIS coverage is model-input coverage, not observed transactions."
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
        f"| MERIS delivered-log model inputs | "
        f"{qc['ledger']['delivered_log_revenue_model_input_rows']} model-input rows | "
        f"{qc['ledger']['delivered_log_revenue_observed_rows']} observed rows | "
        "**model input, not observed** |"
    )
    lines.append(
        f"| MERIS quality-class price rows | "
        f"{qc['ledger']['delivered_log_revenue_quality_rows']} rows | "
        f"{qc['sample_frame']['price_deck_cells']} price-deck cells "
        "(45 species x 7 products x 2 series) | "
        f"{_percent(qc['ledger']['delivered_log_revenue_quality_rows'] / qc['sample_frame']['price_deck_cells'])} |"
    )
    lines.append(
        f"| MERIS species/DBH curve rows | "
        f"{qc['ledger']['delivered_log_revenue_dbh_rows']} rows | "
        f"{qc['sample_frame']['dbh_curve_cells']} curve cells "
        f"({qc['sample_frame']['dbh_curve_species']} species x "
        f"{qc['sample_frame']['dbh_curve_classes']} DBH x "
        f"{qc['sample_frame']['dbh_curve_series']} series) | "
        f"{_percent(qc['ledger']['delivered_log_revenue_dbh_rows'] / qc['sample_frame']['dbh_curve_cells'])} |"
    )
    lines.append(
        f"| MERIS cost-deck tarification zones | "
        f"{qc['ledger']['zones_covered']} | "
        f"{qc['sample_frame']['tarification_zones']} zones | "
        f"{_percent(qc['zone_share']['share'])} |"
    )
    lines.append(
        f"| MERIS usable silviculture treatments | "
        f"{qc['ledger']['treatments_covered']} | "
        f"{qc['sample_frame']['treatments']} treatments | "
        f"{_percent(qc['treatment_share']['share'])} |"
    )
    lines.append(
        f"| MERIS current Crown stumpage rate rows | "
        f"{qc['ledger']['current_stumpage_rate_rows']} | "
        f"{qc['sample_frame']['stumpage_rate_rows']:,} zone-species rows | "
        "**not in full model deck** |"
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
    lines.append("### QC MERIS full model-input deck detail")
    lines.append("")
    lines.append(
        f"- {qc['ledger']['rows']} rows, all `model_input` and `forest_sector`; "
        f"{qc['ledger']['zones_covered']} cost-deck zones and "
        f"{qc['ledger']['treatments_covered']} usable silviculture treatments. "
        f"Cost components: {', '.join(qc['ledger']['cost_components'])}."
    )
    lines.append(
        f"- {qc['ledger']['delivered_log_revenue_rows']} "
        f"`delivered_log_revenue` rows are mill-gate model inputs "
        f"({qc['ledger']['delivered_log_revenue_observed_rows']} observed): "
        f"{qc['ledger']['delivered_log_revenue_quality_rows']} quality-class "
        f"price-deck rows and {qc['ledger']['delivered_log_revenue_dbh_rows']} "
        "species/DBH curve rows. They are not mill settlements and are not "
        "monetised."
    )
    lines.append(
        "- Quality classes in the price deck: "
        + ", ".join(
            f"`{quality}` {count}"
            for quality, count in qc["ledger"]["quality_rows_by_class"].items()
        )
        + ". `PATE` is the 0.0100 placeholder and yields no pulpwood row."
    )
    lines.append(
        f"- DBH curves cover {qc['sample_frame']['dbh_curve_species']} SEPM "
        f"species, {qc['sample_frame']['dbh_curve_classes']} DBH classes "
        f"({qc['ledger']['dbh_min_cm']:.0f}-{qc['ledger']['dbh_max_cm']:.0f} cm), "
        f"and {qc['sample_frame']['dbh_curve_series']} series; "
        f"`{qc['sample_frame']['dbh_curve_profile']}` is used because the three "
        "shipped profiles are byte-identical. The curve is a composite "
        "sciage/chips/by-product value, so it is booked as `mixed_log` with "
        "`quality=NA`."
    )
    lines.append(
        "- The two price series are alternative scenarios separated by licence "
        "suffix `C` (courant) and `T` (tendance); they are not independent "
        "observations and are not summed."
    )
    lines.append(
        "- `harvest` is loading-only (`partial=loading_only`), `road` excludes "
        "tertiary road, and `silviculture` is a CAD/ha treatment catalogue. "
        "The deck has no Crown perspective and no mirrors."
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
        f"- {qc['ledger']['delivered_log_revenue_model_input_rows']} "
        "`delivered_log_revenue` rows are model inputs; "
        f"{qc['ledger']['delivered_log_revenue_observed_rows']} are observed. "
        "No observed delivered-log revenue was invented."
    )
    lines.append(
        "- Model-input rows are counted and reported but excluded from every "
        "monetary total."
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
