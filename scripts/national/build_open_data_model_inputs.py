#!/usr/bin/env python3
"""Publish the exact derived model inputs for the BC open-data net-value model.

The bundle contains only the rows and parameters consumed by
``workflows/national/harvest/open_data_net_value.py``:

    bc_price_panel_used.csv
    bc_cost_inputs_used.csv
    bc_cost_parameters_used.csv
    silviculture_inputs_used.csv
    qc_reference_used.csv
    zone_region_mapping.csv
    sources.csv
    input_manifest.json
    README.md
    RUN.md

Raw copyrighted reports are cited by URL and SHA-256 but are not redistributed.
The builder fails closed when a source row, parameter, mapping or headline
changes unexpectedly.

Usage
-----
    .venv/bin/python scripts/national/build_open_data_model_inputs.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest import bc_open_cost_model as cost_model
from workflows.national.harvest import open_data_net_value as net_value

HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
MODEL_DIR = HARVEST_BASIS_DIR / "open_data_net_value"
DEFAULT_BUNDLE_DIR = MODEL_DIR / "model_inputs"

COST_ESTIMATES_CSV = HARVEST_BASIS_DIR / "bc_open_cost_model/bc_open_cost_estimates.csv"
COST_PARAMETERS_CSV = (
    HARVEST_BASIS_DIR / "bc_open_cost_model/bc_open_cost_parameters.csv"
)
LOG_MARKET_PRICES_CSV = HARVEST_BASIS_DIR / "bc_log_market/bc_log_market_prices.csv"
APPRAISAL_CSV = HARVEST_BASIS_DIR / "bc_appraisal_costs/bc_appraisal_costs.csv"
PROCUREMENT_CSV = (
    HARVEST_BASIS_DIR / "procurement_benchmark/qc_procurement_cost_benchmark.csv"
)
QC_SURVEY_MANIFEST = HARVEST_BASIS_DIR / "qc_cost_survey_2022/manifest.json"
APPRAISAL_MANIFEST = HARVEST_BASIS_DIR / "bc_appraisal_costs/manifest.json"
MODEL_SOURCE = REPO_ROOT / "workflows/national/harvest/open_data_net_value.py"
COST_MODEL_SOURCE = REPO_ROOT / "workflows/national/harvest/bc_open_cost_model.py"

BUNDLE_VERSION = "1.0.0"
MODEL_VERSION = net_value.MODEL_VERSION
BUNDLE_MANIFEST_JSON = "input_manifest.json"

PRICE_PANELS: tuple[tuple[str, str, str], ...] = (
    ("3_month", "2025-12-31", "primary_3_month_2025-12"),
    ("1_month", "2025-12-31", "sensitivity_1_month_2025-12"),
)
EXPECTED_PRICE_COUNTS = {
    "primary_3_month_2025-12": {"rows": 94, "used": 89, "excluded": 5},
    "sensitivity_1_month_2025-12": {"rows": 96, "used": 91, "excluded": 5},
}
EXPECTED_SILVICULTURE_USED = 7
EXPECTED_COAST_SILVICULTURE_EXCLUDED = 15
EXPECTED_COST_ROWS = 945

HEADLINE = {
    "median_base_net_model_cad_m3": 48.84,
    "min_base_net_model_cad_m3": -23.38,
    "max_base_net_model_cad_m3": 86.85,
    "negative_net_base_rows": 168,
    "south_west_ground_net_model_base_median_cad_m3": 64.40,
    "north_west_ground_net_model_base_median_cad_m3": 57.76,
    "qc_cost_spread_after_transfers_cad_m3": 0.81,
}

CORE_OUTPUTS = (
    "bc_open_net_value.csv",
    "bc_open_net_value_summary.csv",
    "bc_open_net_value_sensitivity.csv",
    "qc_open_reference.csv",
)


class BundleError(ValueError):
    """A bundle input or published value violates the documented contract."""


@dataclass(frozen=True)
class SourceRow:
    source_id: str
    title: str
    publisher: str
    url: str
    retrieved_utc: str
    licence_terms: str
    citation: str
    used_for: str
    used_in_model: str
    source_sha256: str
    local_filename: str
    raw_redistributed: str = "false"

    def as_row(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "publisher": self.publisher,
            "url": self.url,
            "retrieved_utc": self.retrieved_utc,
            "licence_terms": self.licence_terms,
            "raw_redistributed": self.raw_redistributed,
            "citation": self.citation,
            "used_for": self.used_for,
            "used_in_model": self.used_in_model,
            "source_sha256": self.source_sha256,
            "local_filename": self.local_filename,
        }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise BundleError(f"missing input file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    if not rows:
        raise BundleError(f"refusing to write empty table: {path}")
    if not fieldnames:
        raise BundleError(f"refusing to write table without columns: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(path: Path, *, in_data_repo: bool = False) -> str | None:
    cwd = REPO_ROOT / "data" if in_data_repo else REPO_ROOT
    relative = path.relative_to(cwd).as_posix()
    result = subprocess.run(
        ["git", "-C", str(cwd), "log", "-1", "--format=%H", "--", relative],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    return commit or None


def _head_commit(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _file_provenance(path: Path, *, in_data_repo: bool = False) -> dict[str, Any]:
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "commit": _git_commit(path, in_data_repo=in_data_repo),
    }


def _is_true(raw: str) -> bool:
    return raw.strip().lower() == "true"


def select_price_panel_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Return the primary and 1-month rows, including explicitly excluded cells."""
    selected: list[dict[str, str]] = []
    for report_basis, period_end, panel_role in PRICE_PANELS:
        vintage = [
            row
            for row in rows
            if row["report_basis"] == report_basis and row["period_end"] == period_end
        ]
        expected = EXPECTED_PRICE_COUNTS[panel_role]
        if len(vintage) != expected["rows"]:
            raise BundleError(
                f"{panel_role} expected {expected['rows']} rows, found {len(vintage)}"
            )
        for row in vintage:
            region = row["region"]
            plausible = row["price_plausibility"] == "ok"
            is_aggregate = _is_true(row["is_aggregate"])
            if region == "coast":
                used = plausible
                consumption_role = (
                    "coast_report_footer_average_support"
                    if used
                    else "coast_outlier_excluded"
                )
                exclusion_reason = "" if used else "price_plausibility_not_ok"
                price_used = row["report_avg_price_cad_m3"] if used else ""
                volume_used = row["report_total_volume_m3"] if used else ""
            elif region == "interior":
                used = plausible and not is_aggregate
                if used:
                    consumption_role = "interior_product_cell"
                    exclusion_reason = ""
                elif is_aggregate:
                    consumption_role = "interior_aggregate_excluded"
                    exclusion_reason = "interior_weighted_average_aggregate"
                else:
                    consumption_role = "interior_outlier_excluded"
                    exclusion_reason = "price_plausibility_not_ok"
                price_used = row["price_cad_m3"] if used else ""
                volume_used = row["volume_m3"] if used else ""
            else:
                raise BundleError(f"unexpected price region: {region!r}")
            selected.append(
                {
                    **row,
                    "panel_role": panel_role,
                    "used_in_model": "true" if used else "false",
                    "consumption_role": consumption_role,
                    "exclusion_reason": exclusion_reason,
                    "outlier_flag": "true" if not plausible else "false",
                    "price_used_cad_m3": price_used,
                    "volume_used_m3": volume_used,
                }
            )

    for panel_role, expected in EXPECTED_PRICE_COUNTS.items():
        panel = [row for row in selected if row["panel_role"] == panel_role]
        used = sum(1 for row in panel if row["used_in_model"] == "true")
        if used != expected["used"]:
            raise BundleError(
                f"{panel_role} expected {expected['used']} used rows, found {used}"
            )
        excluded = len(panel) - used
        if excluded != expected["excluded"]:
            raise BundleError(
                f"{panel_role} expected {expected['excluded']} excluded rows, "
                f"found {excluded}"
            )
    if any(
        row["used_in_model"] == "true" and row["price_plausibility"] != "ok"
        for row in selected
    ):
        raise BundleError("an implausible price cell is marked used")

    selected.sort(
        key=lambda row: (
            row["panel_role"],
            row["region"],
            row["report_id"],
            row["log_class"],
            row["species"],
            row["end_use"],
            row["is_aggregate"],
        )
    )
    return selected


def _phase_parameter_ids(
    rate_group: str, appraisal_region: str, system: str
) -> set[str]:
    if system == "heli":
        return {
            "heli_allowance",
            "slope_coefficient",
            "piece_coefficient",
            "volume_coefficient",
        }
    phases = (
        ("feller_buncher", "forwarder", "processor", "loader")
        if system == "ground" and appraisal_region == "Coast"
        else ("feller_buncher", "grapple_skidder", "processor", "loader")
        if system == "ground"
        else ("feller_buncher", "yarder", "processor", "loader")
    )
    ids = {f"rate_{rate_group}_{phase}" for phase in phases} | {
        f"productivity_{rate_group}_{phase}" for phase in phases
    }
    return ids | {
        "slope_coefficient",
        "piece_coefficient",
        "volume_coefficient",
        "uncertainty_productivity",
    }


def _haul_parameter_ids(rate_group: str, appraisal_region: str) -> set[str]:
    ids = {
        f"rate_{rate_group}_truck_tractor",
        f"rate_{rate_group}_trailer",
        "in_block_speed",
        "road_speed",
        "delay_and_load",
        "payload",
    }
    if appraisal_region == "Interior":
        ids.add("iam_haul_coefficient")
    return ids


def build_cost_rows(
    cost_rows: Sequence[dict[str, str]],
    parameter_rows: Sequence[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Attach formula, parameter and source IDs to each cost-grid row."""
    parameters = {row["parameter_id"]: row for row in parameter_rows}
    if len(parameters) != len(parameter_rows):
        raise BundleError("duplicate parameter_id in the cost parameter register")

    enriched: list[dict[str, str]] = []
    referenced: set[str] = set()
    for row in cost_rows:
        parameter_ids = _phase_parameter_ids(
            row["rate_group"], row["appraisal_region"], row["system"]
        ) | _haul_parameter_ids(row["rate_group"], row["appraisal_region"])
        missing = parameter_ids - set(parameters)
        if missing:
            raise BundleError(
                f"cost row {row['scenario_id']} references unknown parameters: "
                f"{sorted(missing)}"
            )
        source_ids = sorted({parameters[item]["source_key"] for item in parameter_ids})
        referenced |= parameter_ids
        logging_formula = (
            "logging_cost_heli"
            if row["system"] == "heli"
            else f"logging_cost_{row['system']}"
        )
        haul_formula = (
            "haul_cost_interior_iam_engineering"
            if row["appraisal_region"] == "Interior"
            else "haul_cost_coast_engineering"
        )
        enriched.append(
            {
                **row,
                "logging_formula_id": logging_formula,
                "haul_formula_id": haul_formula,
                "net_value_formula_id": "net_model_margin_v1",
                "parameter_ids": "|".join(sorted(parameter_ids)),
                "source_ids": "|".join(source_ids),
            }
        )

    parameter_subset = [parameters[parameter_id] for parameter_id in sorted(referenced)]
    return enriched, parameter_subset


def build_silviculture_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Return the seven loaded Interior rows plus explicit Coast exclusions."""
    used = [
        row
        for row in rows
        if row["source_series"] == net_value.SILVICULTURE_SERIES
        and row["component_group"] == "silviculture"
        and row["component"] == net_value.SILVICULTURE_COMPONENT
        and row["cost_basis"] == "CAD/m3"
        and row["zone_basis"] == "forest_zone"
    ]
    if len(used) != EXPECTED_SILVICULTURE_USED:
        raise BundleError(
            f"expected {EXPECTED_SILVICULTURE_USED} Interior silviculture rows, "
            f"found {len(used)}"
        )

    coast = [
        row
        for row in rows
        if row["region"] == "Coast"
        and row["component_group"] == "silviculture"
        and row["component"] == net_value.SILVICULTURE_COMPONENT
        and row["cost_basis"] == "CAD/ha"
        and row["source_series"] == "CAM 2026"
    ]
    if len(coast) != EXPECTED_COAST_SILVICULTURE_EXCLUDED:
        raise BundleError(
            f"expected {EXPECTED_COAST_SILVICULTURE_EXCLUDED} Coast silviculture "
            f"rows, found {len(coast)}"
        )

    output: list[dict[str, str]] = []
    for row in used:
        applies = net_value.price_region_for_zone(row["zone"]) == "Interior"
        output.append(
            {
                **row,
                "source_id": "bc_silv_oct_2026",
                "loaded_into_model": "true",
                "applied_as_deduction": "true" if applies else "false",
                "exclusion_reason": (
                    ""
                    if applies
                    else "zone_is_coast_priced_coast_silviculture_not_converted"
                ),
            }
        )
    for row in coast:
        output.append(
            {
                **row,
                "source_id": "bc_cam_2026",
                "loaded_into_model": "false",
                "applied_as_deduction": "false",
                "exclusion_reason": (
                    "coast_cam_table_5_6_publishes_cad_per_ha_only_"
                    "no_cad_per_m3_conversion"
                ),
            }
        )
    return output


def build_qc_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Return the exact QC SEPM cost-spread row with raw-source citation."""
    selected = [row for row in rows if row["benchmark_id"] == "qc_2022_sepm_total"]
    if len(selected) != 1:
        raise BundleError(f"expected one qc_2022_sepm_total row, found {len(selected)}")
    source = selected[0]
    if abs(float(source["cost_spread_after_transfers_cad_m3"]) - 0.81) > 0.001:
        raise BundleError("QC SEPM after-transfer cost spread is not 0.81")
    manifest = json.loads(QC_SURVEY_MANIFEST.read_text(encoding="utf-8"))
    main_source = next(
        item for item in manifest["sources"] if item["source_id"] == "main_2022"
    )
    return [
        {
            **source,
            "source_id": "qc_mrnf_2022_main",
            "source_title": main_source["title"],
            "source_url": main_source["url"],
            "source_sha256": main_source["sha256"],
            "retrieved_utc": main_source["retrieved_utc"],
            "used_in_model": "true",
            "model_role": "qc_reference_not_bc_net_value",
        }
    ]


def build_zone_mapping() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for zone, rate_group, appraisal_region in cost_model.ZONES:
        price_region = net_value.price_region_for_zone(zone)
        if price_region != appraisal_region:
            raise BundleError(
                f"price/appraisal region mismatch for {zone}: "
                f"{price_region} vs {appraisal_region}"
            )
        includes_silviculture = price_region == "Interior"
        rows.append(
            {
                "zone": zone,
                "rate_group": rate_group,
                "appraisal_region": appraisal_region,
                "price_region": price_region,
                "price_region_basis": "appraisal_region_selects_price_and_haul",
                "silviculture_deduction": (
                    "true" if includes_silviculture else "false"
                ),
                "silviculture_component": (
                    "interior_basic_silviculture"
                    if includes_silviculture
                    else net_value.COAST_SILVICULTURE_COMPONENT
                ),
                "source_ids": "|".join(
                    (
                        "bc_iam_2026",
                        "bc_cam_2026",
                        "bc_silv_oct_2026",
                        "tla_rate_guide_2026_08",
                    )
                    if includes_silviculture
                    else (
                        "bc_iam_2026",
                        "bc_cam_2026",
                        "tla_rate_guide_2026_08",
                    )
                ),
                "notes": (
                    "North West Zone is Interior for price, haul and silviculture "
                    "but uses the coastal/northwest hourly rate group."
                    if zone == "North West Zone"
                    else "South West Zone is the Coast-priced forest zone; Coast "
                    "silviculture is not deducted because CAM publishes CAD/ha only."
                    if zone == "South West Zone"
                    else "Interior-priced forest zone."
                ),
            }
        )
    if len(rows) != len(net_value.EXPECTED_ZONES):
        raise BundleError("zone mapping row count does not match the model contract")
    return rows


def build_sources(
    price_rows: Sequence[dict[str, str]],
    appraisal_rows: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    """Build the raw-source register without redistributing any raw report."""
    sources: dict[str, SourceRow] = {}

    def add(source: SourceRow) -> None:
        existing = sources.get(source.source_id)
        if existing and existing.url != source.url:
            raise BundleError(f"source_id {source.source_id} maps to conflicting URLs")
        sources[source.source_id] = source

    titles = {
        ("coast", "3_month", "old_growth"): (
            "BC Coast Log Market Report, 3-Month Period Ending December 2025: "
            "Old Growth"
        ),
        ("coast", "3_month", "second_growth"): (
            "BC Coast Log Market Report, 3-Month Period Ending December 2025: "
            "Second Growth"
        ),
        ("coast", "1_month", "old_growth"): (
            "BC Coast Log Market Report, 1-Month Period Ending December 2025: "
            "Old Growth"
        ),
        ("coast", "1_month", "second_growth"): (
            "BC Coast Log Market Report, 1-Month Period Ending December 2025: "
            "Second Growth"
        ),
        ("interior", "3_month", "not_reported"): (
            "BC Interior Log Market Report, 3-Month Period Ending December 2025"
        ),
        ("interior", "1_month", "not_reported"): (
            "BC Interior Log Market Report, 1-Month Period Ending December 2025"
        ),
    }
    seen_reports: set[str] = set()
    for row in price_rows:
        report_id = row["report_id"]
        if report_id in seen_reports:
            continue
        seen_reports.add(report_id)
        key = (row["region"], row["report_basis"], row["log_class"])
        title = titles.get(key)
        if title is None:
            raise BundleError(f"no source title for price report {report_id}")
        retrieved = row["retrieved_utc"][:10]
        add(
            SourceRow(
                source_id=report_id,
                title=title,
                publisher="BC Ministry of Forests, Timber Pricing Branch",
                url=row["source_url"],
                retrieved_utc=row["retrieved_utc"],
                licence_terms=(
                    "BC Crown copyright; public log-market report. Raw PDF "
                    "cited but not redistributed; commercial reuse requires "
                    "permission from the rights holder."
                ),
                citation=(
                    "Government of British Columbia, Ministry of Forests, "
                    f"Timber Pricing Branch. {title}. {row['source_url']} "
                    f"(retrieved {retrieved})."
                ),
                used_for=f"{row['report_basis']} {row['region']} price panel",
                used_in_model="true",
                source_sha256=row["source_sha256"],
                local_filename=row["source_document"],
            )
        )

    appraisal_sources = {
        "iam_2026_master_b.pdf": (
            "bc_iam_2026",
            "Interior Appraisal Manual, effective 2026-07-01",
            "BC Timber Pricing Branch, Ministry of Forests",
            "IAM-derived logging, slope, piece-size, volume and haul parameters.",
        ),
        "cam_2026_master.pdf": (
            "bc_cam_2026",
            "Coast Appraisal Manual, effective 2026-01-01",
            "BC Timber Pricing Branch, Ministry of Forests",
            "Coast appraisal rules and Coast basic silviculture CAD/ha table.",
        ),
        "basic_silv_costs_oct_26.pdf": (
            "bc_silv_oct_2026",
            "Interior Basic Silviculture Costs, effective 2026-10-01",
            "BC Timber Pricing Branch, Ministry of Forests",
            "Interior basic silviculture CAD/m3 allowance by forest zone.",
        ),
    }
    appraisal_manifest = json.loads(APPRAISAL_MANIFEST.read_text(encoding="utf-8"))
    appraisal_provenance = {
        item["file"]: item for item in appraisal_manifest["sources"]
    }
    seen_appraisal: set[str] = set()
    for row in appraisal_rows:
        document = row["source_document"]
        if document not in appraisal_sources:
            continue
        if document in seen_appraisal:
            continue
        seen_appraisal.add(document)
        key = appraisal_sources[document]
        source_id, title, publisher, used_for = key
        provenance = appraisal_provenance.get(document)
        if provenance is None:
            raise BundleError(f"no appraisal manifest provenance for {document}")
        retrieved = provenance["retrieved_utc"]
        if not retrieved:
            raise BundleError(f"appraisal source has no retrieval date: {document}")
        add(
            SourceRow(
                source_id=source_id,
                title=title,
                publisher=publisher,
                url=row["source_url"],
                retrieved_utc=retrieved,
                licence_terms=(
                    "BC Crown copyright; public PDF. Raw report cited but not "
                    "redistributed; commercial reuse requires permission."
                ),
                citation=(
                    f"Government of British Columbia, {publisher}. {title}. "
                    f"{row['source_url']} (retrieved {retrieved[:10]})."
                ),
                used_for=used_for,
                used_in_model="true",
                source_sha256=row["source_sha256"],
                local_filename=document,
            )
        )

    missing_appraisal = set(appraisal_sources) - seen_appraisal
    if missing_appraisal:
        raise BundleError(
            f"appraisal source rows missing from the source register: "
            f"{sorted(missing_appraisal)}"
        )

    for source in cost_model.SOURCES:
        source_id = source["source_key"]
        if source_id in sources:
            continue
        used_in_model = "false" if source_id == "bc_bcts_results" else "true"
        add(
            SourceRow(
                source_id=source_id,
                title=f"{source['title']} ({source['year']})",
                publisher=source["publisher"],
                url=source["url"],
                retrieved_utc=source["retrieved_utc"],
                licence_terms=source["licence"],
                citation=(
                    f"{source['publisher']}. {source['title']} "
                    f"({source['year']}). {source['url']} "
                    f"(retrieved {source['retrieved_utc'][:10]})."
                ),
                used_for=source["used_for"],
                used_in_model=used_in_model,
                source_sha256=source["sha256"],
                local_filename="",
            )
        )

    qc_manifest = json.loads(QC_SURVEY_MANIFEST.read_text(encoding="utf-8"))
    main_source = next(
        item for item in qc_manifest["sources"] if item["source_id"] == "main_2022"
    )
    add(
        SourceRow(
            source_id="qc_mrnf_2022_main",
            title=main_source["title"],
            publisher="Gouvernement du Quebec, Ministere des Ressources "
            "naturelles et des Forets",
            url=main_source["url"],
            retrieved_utc=main_source["retrieved_utc"],
            licence_terms=(
                "Public-reference Quebec government report; copyrighted. Raw "
                "PDF cited but not redistributed."
            ),
            citation=(
                "Gouvernement du Quebec, Ministere des Ressources naturelles "
                f"et des Forets. {main_source['title']}. {main_source['url']} "
                f"(retrieved {main_source['retrieved_utc'][:10]})."
            ),
            used_for="QC 2022 SEPM purchased-wood channel cost spread (T2 vs T20).",
            used_in_model="true",
            source_sha256=main_source["sha256"],
            local_filename=main_source["filename"],
        )
    )

    if any(source.raw_redistributed != "false" for source in sources.values()):
        raise BundleError("bundle may not redistribute a raw source")
    return [sources[key].as_row() for key in sorted(sources)]


def _row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _render_readme(rows: Mapping[str, int], headline: Mapping[str, Any]) -> str:
    return f"""# Open-data BC net-value model inputs

This directory is the self-contained, derived model-input bundle for the
corrected BC open-data modeled-margin layer (`open_data_net_value`, model
version `{MODEL_VERSION}`). It publishes **code plus the exact derived input
rows** consumed by the model. Raw copyrighted reports are cited in
`sources.csv` by URL, hash and retrieval date; **no raw PDF is redistributed**.

## Headline reproduced from this bundle

- Median base `net_model`: **{headline["median_base_net_model_cad_m3"]:.2f} CAD/m3**
- Base `net_model` range: **{headline["min_base_net_model_cad_m3"]:.2f} to {headline["max_base_net_model_cad_m3"]:.2f} CAD/m3**
- Negative base rows: **{headline["negative_net_base_rows"]} / {rows["bc_cost_inputs_used.csv"]}**
- South West Zone ground median: **{headline["south_west_ground_net_model_base_median_cad_m3"]:.2f} CAD/m3**
- North West Zone ground median: **{headline["north_west_ground_net_model_base_median_cad_m3"]:.2f} CAD/m3**
- QC SEPM after-transfer cost spread: **{headline["qc_cost_spread_after_transfers_cad_m3"]:.2f} CAD/m3** (separate reference, not a net value)

## Files

| File | Rows | Purpose |
|---|---:|---|
| `bc_price_panel_used.csv` | {rows["bc_price_panel_used.csv"]} | Exact primary 3-month and sensitivity 1-month 2025-12 price rows; excluded Interior aggregates flagged. |
| `bc_cost_inputs_used.csv` | {rows["bc_cost_inputs_used.csv"]} | Full 945-row logging/haul cost grid with formula, parameter and source IDs. |
| `bc_cost_parameters_used.csv` | {rows["bc_cost_parameters_used.csv"]} | Parameter register subset referenced by the cost grid. |
| `silviculture_inputs_used.csv` | {rows["silviculture_inputs_used.csv"]} | Seven Interior basic silviculture rows plus explicit Coast CAM CAD/ha exclusions. |
| `qc_reference_used.csv` | {rows["qc_reference_used.csv"]} | Exact QC 2022 SEPM procurement cost-spread row. |
| `zone_region_mapping.csv` | {rows["zone_region_mapping.csv"]} | Zone to rate group, appraisal region, price region and silviculture rule. |
| `sources.csv` | {rows["sources.csv"]} | Raw-source citations, URLs, SHA-256, retrieval dates and licence terms. |
| `input_manifest.json` | n/a | SHA-256 and row counts for every bundle file, headline outputs and provenance. |
| `README.md` | n/a | This file. |
| `RUN.md` | n/a | Exact reproduction commands. |

## Raw sources cited but not redistributed

No raw report is present in this bundle. `sources.csv` cites each one with URL,
retrieval date and SHA-256:

- BC Interior and Coast appraisal manuals, and the Interior basic silviculture
  costs table (BC Crown copyright);
- BC Coast and Interior log-market reports (BC Crown copyright);
- TLA/ILA/NWLA/MNP 2026 timber-harvest cost-model rate guide (publisher
  copyright);
- FPInnovations/FERIC case studies and the 9-axle B-train report (publisher
  copyright);
- BC CVSE approved logging-truck configurations (BC Crown copyright);
- Quebec MRNF 2022 forest-industry survey report (Quebec government copyright).

Commercial reuse or redistribution of those raw reports requires permission
from the rights holder. The published derived tables are sufficient to
reproduce the headline.

## Labels

- `input_publication=derived_model_inputs`
- `raw_source_redistribution=false`
- `evidence=model_estimate`
- `observed=false`
- `policy_grade=false`
- `net_value_status=model_estimate`

This bundle is not an observed transaction, a mill profit, an official net
value, or a legal/AAC/policy statement. No model result, UI, or public OSeCAN
artifact is changed by publishing it.
"""


def _render_run() -> str:
    return """# Reproduce the headline from this bundle

Run from the repository root:

```bash
.venv/bin/python scripts/national/reproduce_open_data_net_value.py
```

The reproducer:

1. verifies the SHA-256 and row count of every file listed in
   `input_manifest.json`;
2. validates the zone/region mapping, source flags, price-panel flags and
   parameter references;
3. runs `workflows/national/harvest/open_data_net_value.py` with
   `inputs_dir` set to this bundle;
4. requires the exact headline:
   - median 48.84 CAD/m3;
   - range -23.38 to 86.85 CAD/m3;
   - 168 / 945 negative base rows;
   - South West ground 64.40 CAD/m3;
   - North West ground 57.76 CAD/m3;
   - QC cost spread 0.81 CAD/m3;
5. requires byte-identical core outputs versus the published model artifacts.

Any mismatch exits non-zero. To rebuild this bundle from the parent corpus:

```bash
.venv/bin/python scripts/national/build_open_data_model_inputs.py
```

To run the focused tests:

```bash
.venv/bin/python -m pytest -q tests/test_open_data_model_inputs_bundle.py
```

Raw report PDFs are not present in this bundle and are not needed to reproduce
the headline. They are cited in `sources.csv` for audit only.
"""


def _verify_bundle(
    bundle_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Run the model from the bundle and require the corrected headline."""
    with tempfile.TemporaryDirectory(
        prefix="odnv_inputs_", dir=REPO_ROOT / "tmp"
    ) as raw_dir:
        out_dir = Path(raw_dir)
        manifest = net_value.build(out_dir, inputs_dir=bundle_dir)
        summary = json.loads(
            (out_dir / net_value.SUMMARY_JSON).read_text(encoding="utf-8")
        )
        summary_rows = _read_csv(out_dir / net_value.SUMMARY_CSV)
        qc = _read_csv(out_dir / net_value.QC_REFERENCE_CSV)[0]
        results = summary["results"]
        observed = {
            "median_base_net_model_cad_m3": round(
                results["median_base_net_model_cad_m3"], 2
            ),
            "min_base_net_model_cad_m3": round(results["min_base_net_model_cad_m3"], 2),
            "max_base_net_model_cad_m3": round(results["max_base_net_model_cad_m3"], 2),
            "negative_net_base_rows": results["negative_net_base_rows"],
            "south_west_ground_net_model_base_median_cad_m3": round(
                next(
                    float(row["net_model_base_median_cad_m3"])
                    for row in summary_rows
                    if row["zone"] == "South West Zone" and row["system"] == "ground"
                ),
                2,
            ),
            "north_west_ground_net_model_base_median_cad_m3": round(
                next(
                    float(row["net_model_base_median_cad_m3"])
                    for row in summary_rows
                    if row["zone"] == "North West Zone" and row["system"] == "ground"
                ),
                2,
            ),
            "qc_cost_spread_after_transfers_cad_m3": round(
                float(qc["cost_spread_after_transfers_cad_m3"]), 2
            ),
        }
        for key, expected in HEADLINE.items():
            if observed[key] != expected:
                raise BundleError(
                    f"headline mismatch for {key}: expected {expected}, "
                    f"observed {observed[key]}"
                )
        output_hashes = {name: sha256_file(out_dir / name) for name in CORE_OUTPUTS}
        for name, digest in output_hashes.items():
            published = MODEL_DIR / name
            if published.is_file() and sha256_file(published) != digest:
                raise BundleError(
                    f"bundle output {name} does not match the published artifact"
                )
        if manifest["validation"]["net_value_rows"] != EXPECTED_COST_ROWS:
            raise BundleError("reproduced row count is not 945")
    return observed, output_hashes


def build_bundle(bundle_dir: Path = DEFAULT_BUNDLE_DIR) -> dict[str, Any]:
    """Write the bundle, reproduce the headline and write the manifest."""
    if bundle_dir.exists() and not bundle_dir.is_dir():
        raise BundleError(f"bundle path is not a directory: {bundle_dir}")

    price_source_rows = _read_csv(LOG_MARKET_PRICES_CSV)
    cost_source_rows = _read_csv(COST_ESTIMATES_CSV)
    parameter_rows = _read_csv(COST_PARAMETERS_CSV)
    appraisal_source_rows = _read_csv(APPRAISAL_CSV)
    procurement_rows = _read_csv(PROCUREMENT_CSV)

    if len(cost_source_rows) != EXPECTED_COST_ROWS:
        raise BundleError(
            f"expected {EXPECTED_COST_ROWS} cost rows, found {len(cost_source_rows)}"
        )

    (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
    price_rows = select_price_panel_rows(price_source_rows)
    cost_rows, parameter_subset = build_cost_rows(cost_source_rows, parameter_rows)
    silviculture_rows = build_silviculture_rows(appraisal_source_rows)
    qc_rows = build_qc_rows(procurement_rows)
    mapping_rows = build_zone_mapping()
    source_rows = build_sources(price_rows, appraisal_source_rows)

    bundle_dir.mkdir(parents=True, exist_ok=True)
    stale_manifest = bundle_dir / net_value.MANIFEST_JSON
    if stale_manifest.is_file() and stale_manifest.name != BUNDLE_MANIFEST_JSON:
        stale_manifest.unlink()
    _write_csv(
        bundle_dir / net_value.BUNDLE_PRICE_PANEL_CSV,
        price_rows,
        tuple(price_rows[0]),
    )
    _write_csv(
        bundle_dir / net_value.BUNDLE_COST_INPUTS_CSV,
        cost_rows,
        tuple(cost_rows[0]),
    )
    _write_csv(
        bundle_dir / "bc_cost_parameters_used.csv",
        parameter_subset,
        tuple(parameter_subset[0]),
    )
    _write_csv(
        bundle_dir / net_value.BUNDLE_SILVICULTURE_CSV,
        silviculture_rows,
        tuple(silviculture_rows[0]),
    )
    _write_csv(
        bundle_dir / net_value.BUNDLE_QC_REFERENCE_CSV,
        qc_rows,
        tuple(qc_rows[0]),
    )
    _write_csv(
        bundle_dir / "zone_region_mapping.csv",
        mapping_rows,
        tuple(mapping_rows[0]),
    )
    _write_csv(
        bundle_dir / "sources.csv",
        source_rows,
        tuple(source_rows[0]),
    )

    observed, output_hashes = _verify_bundle(bundle_dir)

    row_counts = {
        premise.name: _row_count(premise)
        for premise in sorted(bundle_dir.glob("*.csv"))
    }
    (bundle_dir / "README.md").write_text(
        _render_readme(row_counts, observed), encoding="utf-8"
    )
    (bundle_dir / "RUN.md").write_text(_render_run(), encoding="utf-8")

    files: list[dict[str, Any]] = []
    for path in sorted(bundle_dir.iterdir()):
        if path.name == BUNDLE_MANIFEST_JSON or path.name.startswith("."):
            continue
        if path.suffix not in {".csv", ".md"}:
            continue
        files.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": _row_count(path) if path.suffix == ".csv" else None,
            }
        )

    parent_inputs = (
        (COST_ESTIMATES_CSV, True),
        (COST_PARAMETERS_CSV, True),
        (LOG_MARKET_PRICES_CSV, True),
        (APPRAISAL_CSV, True),
        (PROCUREMENT_CSV, True),
        (QC_SURVEY_MANIFEST, True),
        (MODEL_SOURCE, False),
        (COST_MODEL_SOURCE, False),
    )
    provenance = {
        "parent_head": _head_commit(REPO_ROOT),
        "data_head": _head_commit(REPO_ROOT / "data"),
        "parent_inputs": [
            _file_provenance(path, in_data_repo=in_data)
            for path, in_data in parent_inputs
        ],
        "bundle_commit": (
            "not_applicable_pre_commit; the bundle is committed in the data "
            "submodule immediately after this manifest is generated"
        ),
    }

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "dataset": "open_data_net_value_model_inputs",
        "model_version": MODEL_VERSION,
        "model_module": "workflows/national/harvest/open_data_net_value.py",
        "labels": {
            "input_publication": "derived_model_inputs",
            "raw_source_redistribution": False,
            "evidence": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "net_value_status": "model_estimate",
            "qc_net_value_status": "separate_reference_not_net_value",
            "combined_bc_qc_national_total": False,
            "model_ui_oscan_read_or_written": False,
        },
        "scope": {
            "jurisdictions": ["BC", "QC"],
            "bc": "modeled mill-delivered margin with open proxy logging/haul costs",
            "qc": "separate purchased-wood channel reference; not a net value",
            "price_primary": "3_month ending 2025-12-31",
            "price_sensitivity": "1_month ending 2025-12-31",
            "silviculture": "Interior basic silviculture CAD/m3 only; Coast excluded",
        },
        "files": files,
        "row_counts": row_counts,
        "headline": observed,
        "reproduction": {
            "command": (
                ".venv/bin/python scripts/national/reproduce_open_data_net_value.py"
            ),
            "core_output_sha256": output_hashes,
            "core_outputs": list(CORE_OUTPUTS),
            "verified": True,
        },
        "provenance": provenance,
        "source_register": {
            "rows": len(source_rows),
            "raw_redistributed": False,
            "source_ids": [row["source_id"] for row in source_rows],
        },
        "manifest_self_hash": "not_applicable_self_reference",
    }
    manifest_path = bundle_dir / BUNDLE_MANIFEST_JSON
    manifest_path.write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=DEFAULT_BUNDLE_DIR,
        help="Directory for the published model-input bundle.",
    )
    args = parser.parse_args(argv)
    manifest = build_bundle(args.bundle_dir)
    total_rows = sum(
        item["rows"] for item in manifest["files"] if item["rows"] is not None
    )
    print(
        "wrote open-data model-input bundle: "
        f"{len(manifest['files'])} files, "
        f"{total_rows} CSV rows, "
        f"median {manifest['headline']['median_base_net_model_cad_m3']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
