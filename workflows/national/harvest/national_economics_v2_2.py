"""National provincial economics v2.2 with direct P1 cost inputs.

The price method is unchanged from v2.1: ``donor_flat`` is the base scenario
and ``stumpage_scaled`` is the sensitivity.  Version 2.2 upgrades only cost
components where a public direct series exists:

* Quebec harvest, haul and road use the MRNF 2022 T20 survey aggregates
  (before transfers; road gross).  Low/high are the published regional
  ranges.  These are survey aggregates, not observed transactions.
* British Columbia road uses the published IAM 2026 Table 4-6 road
  maintenance CAD/m3 allowance.  It is a maintenance-only appraisal
  allowance; CAD/km construction values are not converted.
* Alberta and Ontario keep the donor costs because the source map has no
  direct public cost series.  No value is invented.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_economics_v2_2
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.harvest import national_economics_v1 as v1
from workflows.national.harvest import national_economics_v2 as v2
from workflows.national.harvest import national_economics_v2_1 as v2_1

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
V1_DIR = HARVEST_BASIS_DIR / "national_economics_v1"
V2_DIR = HARVEST_BASIS_DIR / "national_economics_v2"
V21_DIR = HARVEST_BASIS_DIR / "national_economics_v2_1"
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_economics_v2_2"

QC_SURVEY_DIR = HARVEST_BASIS_DIR / "qc_cost_survey_2022"
BC_APPRAISAL_DIR = HARVEST_BASIS_DIR / "bc_appraisal_costs"
QC_SURVEY_CSV = QC_SURVEY_DIR / "qc_cost_revenue_survey.csv"
QC_SURVEY_MANIFEST = QC_SURVEY_DIR / "manifest.json"
BC_APPRAISAL_CSV = BC_APPRAISAL_DIR / "bc_appraisal_costs.csv"
BC_APPRAISAL_MANIFEST = BC_APPRAISAL_DIR / "manifest.json"

PROVINCES = v1.PROVINCES
PROVINCE_NAMES = v1.PROVINCE_NAMES
ALLOCATION_CSV = v1.PROVINCIAL_ALLOCATION_CSV

MODEL_ID = "national_economics_v2_2"
ARTIFACT_VERSION = "2.2.0"

BASE_SCENARIO = "donor_flat"
SENSITIVITY_SCENARIO = "stumpage_scaled"
SCENARIOS = (BASE_SCENARIO, SENSITIVITY_SCENARIO)
SCENARIO_ROLE = {
    BASE_SCENARIO: "base",
    SENSITIVITY_SCENARIO: "sensitivity",
}
SCENARIO_ORDER = {BASE_SCENARIO: 0, SENSITIVITY_SCENARIO: 1}

COST_INPUTS_CSV = "national_cost_inputs.csv"
MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
COMPARISON_CSV = "v2_1_v2_2_comparison.csv"
TIER_CSV = "tier_coverage.csv"
ASSUMPTIONS_CSV = "assumptions_donor_register.csv"
SOURCES_CSV = "sources.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NATIONAL_ECONOMICS_V2_2_REPORT.md"
MANIFEST_JSON = "manifest.json"

EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
NET_VALUE_STATUS = "model_estimate"
COMBINED_NATIONAL_NET_VALUE = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"
STRICT_OPEN = "false"

TIER_QC_SURVEY = "reported_survey_aggregate"
TIER_BC_APPRAISAL = "appraisal_allowance"
TIER_BC_SILVICULTURE = "reported_average"
TIER_PROXY = "proxy_model"
TIER_DONOR = "donor_proxy"

QC_SOURCE_KEY = "qc_cost_survey_2022"
BC_ROAD_SOURCE_KEY = "bc_appraisal_costs_2026"
BC_COST_SOURCE_KEY = "bc_open_cost_model"
BC_SILVICULTURE_SOURCE_KEY = "bc_appraisal_silviculture_2026_10"

QC_SOURCE_TITLE = "Quebec MRNF 2022 forest cost and revenue survey aggregates"
QC_SOURCE_URL = (
    "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/entreprises/"
    "statistiques/RA_couts_approvisionnement_transformation_revenus_2022.pdf"
)
BC_APPRAISAL_SOURCE_TITLE = "BC Interior Appraisal Manual 2026 road maintenance"
BC_APPRAISAL_SOURCE_URL = (
    "https://www2.gov.bc.ca/gov/content/industry/forestry/"
    "competitive-forest-industry/timber-pricing/interior-timber-pricing/"
    "interior-appraisal-manual"
)
BC_COST_SOURCE_TITLE = "Open-data BC logging and truck-haul cost model"
BC_COST_SOURCE_URL = (
    "https://www.tla.ca/wp-content/uploads/2026/08/"
    "Timber-Harvest-Cost-Model-Rate-Sheet_Aug-10-2026.pdf"
)
BC_SILVICULTURE_SOURCE_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/interior-timber-pricing/interior-appraisal-manual/"
    "iam_2026_master_b.pdf"
)

SOURCE_URLS = {
    QC_SOURCE_KEY: QC_SOURCE_URL,
    BC_ROAD_SOURCE_KEY: BC_APPRAISAL_SOURCE_URL,
    BC_COST_SOURCE_KEY: BC_COST_SOURCE_URL,
    BC_SILVICULTURE_SOURCE_KEY: BC_SILVICULTURE_SOURCE_URL,
}

RESTRICTED_MARKERS = ("confidential", "restricted", "microdata", "private_data")

AB_ON_NO_DIRECT_SERIES = frozenset({"AB", "ON"})


class NationalEconomicsV22Error(ValueError):
    """Raised when an input or result violates the v2.2 contract."""


@dataclass(frozen=True)
class CostComponent:
    """One parsed cost input for a province and component."""

    province: str
    component: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    source_key: str
    source: str
    source_url: str
    source_year: str
    tier: str
    direct_series_status: str
    donor_province: str
    notes: str

    def __post_init__(self) -> None:
        _require_ordered(
            self.low_cad_m3,
            self.base_cad_m3,
            self.high_cad_m3,
            f"{self.province}/{self.component}",
        )
        for value in (self.low_cad_m3, self.base_cad_m3, self.high_cad_m3):
            if not math.isfinite(value) or value <= 0.0:
                raise NationalEconomicsV22Error(
                    f"non-positive cost for {self.province}/{self.component}: {value}"
                )


def _fmt(value: float, digits: int = 4) -> str:
    if not math.isfinite(value):
        raise NationalEconomicsV22Error(f"non-finite value: {value}")
    return f"{value:.{digits}f}"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise NationalEconomicsV22Error(f"missing input file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise NationalEconomicsV22Error(f"empty input table: {path}")
    return rows


def _require_columns(
    rows: Sequence[Mapping[str, str]], required: Iterable[str], source: Path
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise NationalEconomicsV22Error(
            f"{source} is missing columns: {sorted(missing)}"
        )


def _number(row: Mapping[str, str], column: str, context: str) -> float:
    raw = row.get(column, "").strip()
    if not raw:
        raise NationalEconomicsV22Error(f"missing {column} in {context}")
    try:
        value = float(raw)
    except ValueError as error:
        raise NationalEconomicsV22Error(
            f"non-numeric {column} in {context}: {raw!r}"
        ) from error
    if not math.isfinite(value):
        raise NationalEconomicsV22Error(f"non-finite {column} in {context}: {value}")
    return value


def _require_ordered(low: float, base: float, high: float, context: str) -> None:
    if not 0.0 < low <= base <= high:
        raise NationalEconomicsV22Error(
            f"unordered low/base/high for {context}: {low}, {base}, {high}"
        )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise NationalEconomicsV22Error(f"refusing to write empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deletion_lines(repository: Path) -> list[str]:
    """Return porcelain lines carrying a staged or unstaged deletion."""
    result = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise NationalEconomicsV22Error(
            f"git status failed in {repository}: {result.stderr.strip()}"
        )
    return [
        line
        for line in result.stdout.splitlines()
        if line and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any unexpected ``D`` in parent or data status."""
    repositories = (REPO_ROOT, REPO_ROOT / "data")
    for repository in repositories:
        deletions = _deletion_lines(repository)
        if deletions:
            raise NationalEconomicsV22Error(
                f"unexpected deletion in {repository}: {deletions[0]}"
            )


def _load_qc_direct_costs() -> dict[str, CostComponent]:
    rows = _read_csv(QC_SURVEY_CSV)
    _require_columns(
        rows,
        (
            "source_id",
            "source_table",
            "region_code",
            "aggregation_level",
            "species_group",
            "product_or_grade",
            "component",
            "value",
            "unit",
        ),
        QC_SURVEY_CSV,
    )
    components = ("harvest", "haul", "road")
    result: dict[str, CostComponent] = {}
    for component in components:
        provincial = [
            row
            for row in rows
            if row["source_table"] == "T20"
            and row["region_code"] == "QC"
            and row["aggregation_level"] == "provincial"
            and row["species_group"] == "sepm"
            and row["product_or_grade"] == "total"
            and row["component"] == component
            and row["unit"] == "CAD/m3"
        ]
        if len(provincial) != 1:
            raise NationalEconomicsV22Error(
                f"expected one QC T20 {component} row, found {len(provincial)}"
            )
        base = _number(provincial[0], "value", f"QC T20 {component}")
        regional = [
            row
            for row in rows
            if row["source_id"] == "regional_2022"
            and row["aggregation_level"] == "regional"
            and row["species_group"] == "sepm"
            and row["product_or_grade"] == "total"
            and row["component"] == component
            and row["unit"] == "CAD/m3"
        ]
        if len(regional) < 2:
            raise NationalEconomicsV22Error(
                f"expected at least two QC regional {component} rows, "
                f"found {len(regional)}"
            )
        values = [
            _number(row, "value", f"QC regional {component} {row['region_code']}")
            for row in regional
        ]
        low, high = min(values), max(values)
        _require_ordered(low, base, high, f"QC {component}")
        result[component] = CostComponent(
            province="QC",
            component=component,
            low_cad_m3=low,
            base_cad_m3=base,
            high_cad_m3=high,
            source_key=QC_SOURCE_KEY,
            source=(
                f"{QC_SOURCE_TITLE}: T20 SEPM synthesis and regional range "
                f"({len(regional)} regions)"
            ),
            source_url=QC_SOURCE_URL,
            source_year="2022",
            tier=TIER_QC_SURVEY,
            direct_series_status="direct_used",
            donor_province="",
            notes=(
                "Reported MRNF 2022 survey aggregate; T20 before transfers; "
                "road is gross access cost (not T5 after-PRCCM). Low/high are "
                "the published regional SEPM minimum/maximum; R09 is a "
                "three-respondent mean and remains flagged in the source. "
                "Not observed transaction data."
            ),
        )
    return result


def _load_bc_road_allowance() -> tuple[CostComponent, float]:
    rows = _read_csv(BC_APPRAISAL_CSV)
    _require_columns(
        rows,
        (
            "jurisdiction",
            "region",
            "source_series",
            "component_group",
            "component",
            "cost_basis",
            "value",
            "zone",
        ),
        BC_APPRAISAL_CSV,
    )
    selected = [
        row
        for row in rows
        if row["jurisdiction"] == "BC"
        and row["region"] == "Interior"
        and row["source_series"] == "IAM 2026"
        and row["component_group"] == "road_management"
        and row["component"] == "road_maintenance"
        and row["cost_basis"] == "CAD/m3"
    ]
    if len(selected) < 3:
        raise NationalEconomicsV22Error(
            f"expected BC IAM road-maintenance CAD/m3 rows, found {len(selected)}"
        )
    groups = {row["zone"] for row in selected}
    if len(groups) != len(selected):
        raise NationalEconomicsV22Error("duplicate BC road-maintenance group rows")
    values = sorted(
        _number(row, "value", f"BC road maintenance {row['zone']}")
        for row in selected
    )
    low, base, high = min(values), statistics.median(values), max(values)
    _require_ordered(low, base, high, "BC road maintenance")
    cam = [
        row
        for row in rows
        if row["jurisdiction"] == "BC"
        and row["region"] == "Coast"
        and row["component_group"] == "tenure_obligation"
        and row["component"] == "road_management"
        and row["cost_basis"] == "CAD/m3"
    ]
    if len(cam) != 1:
        raise NationalEconomicsV22Error(
            f"expected one CAM road-management CAD/m3 row, found {len(cam)}"
        )
    cam_value = _number(cam[0], "value", "CAM road management")
    return (
        CostComponent(
            province="BC",
            component="road",
            low_cad_m3=low,
            base_cad_m3=base,
            high_cad_m3=high,
            source_key=BC_ROAD_SOURCE_KEY,
            source=(
                "BC Interior Appraisal Manual 2026 Table 4-6 road maintenance "
                f"({len(selected)} road groups)"
            ),
            source_url=BC_APPRAISAL_SOURCE_URL,
            source_year="2026",
            tier=TIER_BC_APPRAISAL,
            direct_series_status="direct_used_partial_road_maintenance",
            donor_province="",
            notes=(
                "Published appraisal allowance, not an observed cost. This is "
                "road maintenance only; IAM/CAM road construction is CAD/km "
                "and was not converted to CAD/m3. CAM scale-based road "
                f"management ({cam_value:.2f} CAD/m3) is a cross-check only. "
                "Not a complete road construction/amortization cost."
            ),
        ),
        cam_value,
    )


def _silviculture_components() -> tuple[CostComponent, CostComponent]:
    allowance = v1.load_silviculture_allowance()
    source_url = BC_SILVICULTURE_SOURCE_URL
    base = CostComponent(
        province="BC",
        component="silviculture",
        low_cad_m3=allowance.low_cad_m3,
        base_cad_m3=allowance.base_cad_m3,
        high_cad_m3=allowance.high_cad_m3,
        source_key=BC_SILVICULTURE_SOURCE_KEY,
        source=allowance.source,
        source_url=source_url,
        source_year="2026",
        tier=TIER_BC_SILVICULTURE,
        direct_series_status="direct_used",
        donor_province="",
        notes=allowance.notes,
    )
    donor = replace(
        base,
        province="",
        tier=TIER_DONOR,
        direct_series_status="not_available_direct_cost_series",
        donor_province="BC",
        notes=(
            "BC Interior basic-silviculture allowance used as the national "
            "donor. No provincial silviculture cost series is materialised "
            "for the recipient jurisdiction."
        ),
    )
    return base, donor


def _donor_logging_haul(province: str, cost: v1.CostProxy) -> CostComponent:
    if province == "BC":
        return CostComponent(
            province=province,
            component="logging_haul",
            low_cad_m3=cost.logging_haul_low_cad_m3,
            base_cad_m3=cost.logging_haul_base_cad_m3,
            high_cad_m3=cost.logging_haul_high_cad_m3,
            source_key=BC_COST_SOURCE_KEY,
            source=BC_COST_SOURCE_TITLE,
            source_url=BC_COST_SOURCE_URL,
            source_year="2026",
            tier=TIER_PROXY,
            direct_series_status="proxy_model_retained",
            donor_province="",
            notes=(
                "BC open proxy logging+haul distribution. No public BC "
                "logging/haul CAD/m3 allowance is materialised."
            ),
        )
    return CostComponent(
        province=province,
        component="logging_haul",
        low_cad_m3=cost.logging_haul_low_cad_m3,
        base_cad_m3=cost.logging_haul_base_cad_m3,
        high_cad_m3=cost.logging_haul_high_cad_m3,
        source_key=BC_COST_SOURCE_KEY,
        source=BC_COST_SOURCE_TITLE,
        source_url=BC_COST_SOURCE_URL,
        source_year="2026",
        tier=TIER_DONOR,
        direct_series_status="not_available_direct_cost_series",
        donor_province="BC",
        notes=(
            "BC donor logging+haul adjusted by terrain and road factors. No "
            "provincial logging/haul cost series is materialised."
            + (
                " Source map indicates no direct public cost series for this "
                "jurisdiction; donor retained and no value invented."
                if province in AB_ON_NO_DIRECT_SERIES
                else ""
            )
        ),
    )


def _donor_road(province: str, cost: v1.CostProxy) -> CostComponent:
    return CostComponent(
        province=province,
        component="road",
        low_cad_m3=cost.road_low_cad_m3,
        base_cad_m3=cost.road_base_cad_m3,
        high_cad_m3=cost.road_high_cad_m3,
        source_key=QC_SOURCE_KEY,
        source=(
            "QC MRNF 2022 T5 road total used as national donor "
            "(before/after PRCCM and 20% high uncertainty)"
        ),
        source_url=QC_SOURCE_URL,
        source_year="2022",
        tier=TIER_DONOR,
        direct_series_status="not_available_direct_cost_series",
        donor_province="QC",
        notes=(
            "QC road allowance used as donor. No direct provincial road "
            "CAD/m3 allowance is materialised."
            + (
                " Source map indicates no direct public cost series for this "
                "jurisdiction; donor retained and no value invented."
                if province in AB_ON_NO_DIRECT_SERIES
                else ""
            )
        ),
    )


def _build_component_inputs(
    costs: Mapping[str, v1.CostProxy],
    qc: Mapping[str, CostComponent],
    bc_road: CostComponent,
    bc_silviculture: CostComponent,
    donor_silviculture: CostComponent,
) -> dict[str, dict[str, CostComponent]]:
    result: dict[str, dict[str, CostComponent]] = {}
    for province in PROVINCES:
        cost = costs[province]
        if province == "QC":
            road = qc["road"]
            silviculture = replace(donor_silviculture, province="QC")
            result[province] = {
                "harvest": qc["harvest"],
                "haul": qc["haul"],
                "road": road,
                "silviculture": silviculture,
            }
            continue
        logging_haul = _donor_logging_haul(province, cost)
        road = bc_road if province == "BC" else _donor_road(province, cost)
        if province == "BC":
            silviculture = bc_silviculture
        else:
            silviculture = replace(donor_silviculture, province=province)
            if province in AB_ON_NO_DIRECT_SERIES:
                silviculture = replace(
                    silviculture,
                    notes=(
                        f"{silviculture.notes} Source map indicates no direct "
                        "public cost series for this jurisdiction; donor "
                        "retained and no value invented."
                    ),
                )
        result[province] = {
            "logging_haul": logging_haul,
            "road": road,
            "silviculture": silviculture,
        }
    return result


def _province_cost_tier(province: str) -> str:
    if province == "BC":
        return "mixed_proxy_model_and_appraisal_allowance"
    if province == "QC":
        return "reported_survey_aggregate_with_donor_silviculture"
    return TIER_DONOR


def _province_direct_status(province: str) -> str:
    if province == "QC":
        return "direct_used_harvest_haul_road; donor_silviculture"
    if province == "BC":
        return (
            "direct_used_road_maintenance; proxy_logging_haul; "
            "reported_silviculture"
        )
    return "not_available_direct_cost_series"


def _apply_cost_inputs(
    base_costs: Mapping[str, v1.CostProxy],
    component_inputs: Mapping[str, Mapping[str, CostComponent]],
) -> dict[str, v1.CostProxy]:
    adjusted: dict[str, v1.CostProxy] = {}
    for province in PROVINCES:
        base = base_costs[province]
        inputs = component_inputs[province]
        if province == "QC":
            harvest = inputs["harvest"]
            haul = inputs["haul"]
            logging_haul_low = harvest.low_cad_m3 + haul.low_cad_m3
            logging_haul_base = harvest.base_cad_m3 + haul.base_cad_m3
            logging_haul_high = harvest.high_cad_m3 + haul.high_cad_m3
            logging_haul_tier = TIER_QC_SURVEY
            qc_harvest_haul = logging_haul_base
            qc_road = inputs["road"].base_cad_m3
            qc_total = qc_harvest_haul + qc_road
            qc_ratio = None
            qc_status = "direct_inputs_used"
        else:
            logging_haul_low = base.logging_haul_low_cad_m3
            logging_haul_base = base.logging_haul_base_cad_m3
            logging_haul_high = base.logging_haul_high_cad_m3
            logging_haul_tier = inputs["logging_haul"].tier
            qc_harvest_haul = base.qc_direct_harvest_haul_cad_m3
            qc_road = base.qc_direct_road_cad_m3
            qc_total = base.qc_direct_total_cad_m3
            qc_ratio = base.qc_crosscheck_ratio
            qc_status = base.qc_crosscheck_status

        road = inputs["road"]
        silviculture = inputs["silviculture"]
        total_low = logging_haul_low + road.low_cad_m3 + silviculture.low_cad_m3
        total_base = (
            logging_haul_base + road.base_cad_m3 + silviculture.base_cad_m3
        )
        total_high = (
            logging_haul_high + road.high_cad_m3 + silviculture.high_cad_m3
        )
        _require_ordered(total_low, total_base, total_high, f"{province} total cost")
        adjusted[province] = replace(
            base,
            logging_haul_low_cad_m3=logging_haul_low,
            logging_haul_base_cad_m3=logging_haul_base,
            logging_haul_high_cad_m3=logging_haul_high,
            road_low_cad_m3=road.low_cad_m3,
            road_base_cad_m3=road.base_cad_m3,
            road_high_cad_m3=road.high_cad_m3,
            silviculture_low_cad_m3=silviculture.low_cad_m3,
            silviculture_base_cad_m3=silviculture.base_cad_m3,
            silviculture_high_cad_m3=silviculture.high_cad_m3,
            total_cost_low_cad_m3=total_low,
            total_cost_base_cad_m3=total_base,
            total_cost_high_cad_m3=total_high,
            logging_haul_tier=logging_haul_tier,
            road_tier=road.tier,
            silviculture_tier=silviculture.tier,
            cost_tier=_province_cost_tier(province),
            qc_direct_harvest_haul_cad_m3=qc_harvest_haul,
            qc_direct_road_cad_m3=qc_road,
            qc_direct_total_cad_m3=qc_total,
            qc_crosscheck_ratio=qc_ratio,
            qc_crosscheck_status=qc_status,
            notes=(
                f"v2.2 cost inputs: {_province_direct_status(province)}. "
                "All values are model inputs; reported survey/allowance rows "
                "are not observed transactions."
            ),
        )
    return adjusted


def _price_source_keys(
    province: str,
    scenario: str,
    reference: v2.StumpageReference,
) -> tuple[str, ...]:
    if province == "BC":
        return ("bc_log_market_2025_12", "national_economics_v1")
    if scenario == BASE_SCENARIO:
        return ("national_economics_v1", "bc_log_market_2025_12")
    if reference.extracted:
        return (
            reference.source_key,
            "bcts_annual_2024_25",
            "bc_log_market_2025_12",
        )
    return ("national_economics_v1", "bc_log_market_2025_12")


def _price_source_years(
    province: str,
    scenario: str,
    reference: v2.StumpageReference,
) -> tuple[str, ...]:
    if province == "BC" or scenario == BASE_SCENARIO:
        return ("2025",)
    if reference.extracted and reference.source_key:
        source = v2.SOURCES.get(reference.source_key)
        if source is not None and len(source.publication_date) >= 4:
            return (source.publication_date[:4], "2025")
    return ("2025",)


def _source_urls(keys: Iterable[str]) -> str:
    urls = []
    for key in keys:
        source = v2.SOURCES.get(key)
        if source is not None:
            urls.append(source.url)
            continue
        url = SOURCE_URLS.get(key)
        if url:
            urls.append(url)
    return ";".join(dict.fromkeys(urls))


def _cost_source_keys(inputs: Mapping[str, CostComponent]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(component.source_key for component in inputs.values())
    )


def _cost_source_titles(inputs: Mapping[str, CostComponent]) -> str:
    return ";".join(
        dict.fromkeys(component.source for component in inputs.values())
    )


def _cost_source_years(inputs: Mapping[str, CostComponent]) -> str:
    return ";".join(
        sorted({component.source_year for component in inputs.values()})
    )


def _cost_notes(inputs: Mapping[str, CostComponent]) -> str:
    return " | ".join(
        f"{component.component}: {component.notes}"
        for component in inputs.values()
    )


def _price_rows(prices: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    refs = v2._references()
    ordered = sorted(
        (dict(row) for row in prices),
        key=lambda row: (
            SCENARIO_ORDER[row["price_scenario"]],
            PROVINCES.index(row["province"]),
        ),
    )
    for row in ordered:
        scenario = row["price_scenario"]
        province = row["province"]
        keys = _price_source_keys(province, scenario, refs[province])
        if province == "BC":
            note = (
                "BC reported 2025-12 3-month mill-delivered average; the "
                "stumpage-scaled method is not applied to BC itself."
            )
        elif scenario == BASE_SCENARIO:
            note = (
                "Flat BC donor price used as the v2.2 base scenario. No "
                "provincial price index is materialised. Not observed revenue."
            )
        elif refs[province].extracted:
            note = (
                "Stumpage/dues/standing-value rate multiplied by the BC "
                "delivered-price/BCTS-stumpage ratio. Sensitivity only: not "
                "the base scenario, not a delivered price and not observed "
                "revenue."
            )
        else:
            note = (
                "No current provincial rate is materialised, so the flat BC "
                "donor is used as the sensitivity fallback. Not observed revenue."
            )
        row.update(
            {
                "scenario_role": SCENARIO_ROLE[scenario],
                "scenario_order": SCENARIO_ORDER[scenario],
                "price_scenario_order": SCENARIO_ORDER[scenario],
                "base_scenario": BASE_SCENARIO,
                "source_keys": ";".join(keys),
                "source_urls": _source_urls(keys),
                "evidence_tier": row["price_tier"],
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
                "notes": note,
            }
        )
        rows.append(row)
    return rows


def _margin_rows(
    margins: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    refs: Mapping[str, v2.StumpageReference],
    component_inputs: Mapping[str, Mapping[str, CostComponent]],
) -> list[dict[str, Any]]:
    price_by_key = {
        (row["province"], row["price_scenario"]): row for row in prices
    }
    rows: list[dict[str, Any]] = []
    for raw in sorted(
        margins,
        key=lambda row: (
            SCENARIO_ORDER[row["price_scenario"]],
            PROVINCES.index(row["province"]),
            int(row["scenario_order"]),
        ),
    ):
        row = dict(raw)
        province = row["province"]
        scenario = row["price_scenario"]
        price = price_by_key[(province, scenario)]
        inputs = component_inputs[province]
        price_keys = _price_source_keys(province, scenario, refs[province])
        cost_keys = _cost_source_keys(inputs)
        all_keys = tuple(dict.fromkeys((*price_keys, *cost_keys)))
        if province == "QC":
            harvest_tier = inputs["harvest"].tier
            haul_tier = inputs["haul"].tier
        else:
            harvest_tier = inputs["logging_haul"].tier
            haul_tier = inputs["logging_haul"].tier
        price_years = _price_source_years(
            province, scenario, refs[province]
        )
        cost_years = tuple(_cost_source_years(inputs).split(";"))
        row.update(
            {
                "scenario_role": SCENARIO_ROLE[scenario],
                "price_scenario_order": SCENARIO_ORDER[scenario],
                "base_scenario": BASE_SCENARIO,
                "source_year": ";".join(
                    sorted({*price_years, *cost_years})
                ),
                "tier": row["cost_tier"],
                "price_low_cad_m3": price["price_low_cad_m3"],
                "price_base_cad_m3": price["price_base_cad_m3"],
                "price_high_cad_m3": price["price_high_cad_m3"],
                "evidence_tier": row["price_tier"],
                "price_source_keys": ";".join(price_keys),
                "price_source_urls": _source_urls(price_keys),
                "cost_source_keys": ";".join(cost_keys),
                "cost_source": _cost_source_titles(inputs),
                "cost_source_urls": _source_urls(cost_keys),
                "cost_source_year": _cost_source_years(inputs),
                "harvest_tier": harvest_tier,
                "haul_tier": haul_tier,
                "direct_cost_series_status": _province_direct_status(province),
                "cost_notes": _cost_notes(inputs),
                "source_keys": ";".join(all_keys),
                "source": ";".join(all_keys),
                "source_urls": _source_urls(all_keys),
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "negative_margin_clipped": "false",
            }
        )
        rows.append(row)
    return rows


def _weighted(
    value_by_province: Mapping[str, float], allocation: Mapping[str, float]
) -> float:
    total_volume = sum(allocation.values())
    if total_volume <= 0.0:
        raise NationalEconomicsV22Error("allocation volume sum is zero")
    return sum(value_by_province[p] * allocation[p] for p in PROVINCES) / total_volume


def _summary_rows(
    margins: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    refs: Mapping[str, v2.StumpageReference],
    allocation: Mapping[str, float],
    volume_reference: Mapping[str, Mapping[str, str]],
    blend: v1.BcPriceBlend,
    ratio: tuple[float, float, float],
    costs: Mapping[str, v1.CostProxy],
    component_inputs: Mapping[str, Mapping[str, CostComponent]],
) -> list[dict[str, Any]]:
    raw = v2_1._summary_rows(
        margins,
        prices,
        refs,
        allocation,
        volume_reference,
        blend,
        ratio,
        costs,
    )
    national_inputs = {
        component.component: component
        for inputs in component_inputs.values()
        for component in inputs.values()
    }
    rows: list[dict[str, Any]] = []
    for item in raw:
        row = dict(item)
        province = row["province"]
        if province == "NATIONAL":
            inputs = national_inputs
            cost_keys = _cost_source_keys(inputs)
            row.update(
                {
                    "harvest_tier": "mixed",
                    "haul_tier": "mixed",
                    "cost_tier": "mixed_direct_and_donor",
                    "direct_cost_series_status": "mixed_direct_and_donor",
                }
            )
        else:
            inputs = component_inputs[province]
            cost_keys = _cost_source_keys(inputs)
            if province == "QC":
                row["harvest_tier"] = inputs["harvest"].tier
                row["haul_tier"] = inputs["haul"].tier
            else:
                row["harvest_tier"] = inputs["logging_haul"].tier
                row["haul_tier"] = inputs["logging_haul"].tier
            row["direct_cost_series_status"] = _province_direct_status(province)
        price_keys = _price_source_keys(
            province if province != "NATIONAL" else "BC",
            row["price_scenario"],
            refs["BC"],
        )
        all_keys = tuple(dict.fromkeys((*price_keys, *cost_keys)))
        if province == "NATIONAL":
            source_year = "2022;2025;2026"
        else:
            price_years = _price_source_years(
                province, row["price_scenario"], refs[province]
            )
            cost_years = tuple(_cost_source_years(inputs).split(";"))
            source_year = ";".join(
                sorted({*price_years, *cost_years})
            )
        row.update(
            {
                "source_year": source_year,
                "tier": row["cost_tier"],
                "cost_source_keys": ";".join(cost_keys),
                "cost_source": _cost_source_titles(inputs),
                "cost_source_urls": _source_urls(cost_keys),
                "cost_source_year": _cost_source_years(inputs),
                "cost_notes": _cost_notes(inputs),
                "source_keys": ";".join(all_keys),
                "source": ";".join(all_keys),
                "source_urls": _source_urls(all_keys),
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            }
        )
        rows.append(row)
    return rows


def _comparison_rows(
    margins: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
) -> list[dict[str, Any]]:
    before_rows = _read_csv(V21_DIR / MARGIN_CSV)
    before = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in before_rows
    }
    current = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for province in (*PROVINCES, "NATIONAL"):
            if province == "NATIONAL":
                before_margin = {
                    case: _weighted(
                        {
                            p: float(
                                before[(p, scenario, case)]["margin_cad_m3"]
                            )
                            for p in PROVINCES
                        },
                        allocation,
                    )
                    for case in ("low", "base", "high")
                }
                current_margin = {
                    case: _weighted(
                        {
                            p: float(
                                current[(p, scenario, case)]["margin_cad_m3"]
                            )
                            for p in PROVINCES
                        },
                        allocation,
                    )
                    for case in ("low", "base", "high")
                }
                before_cost = _weighted(
                    {
                        p: float(
                            before[(p, scenario, "base")]["total_cost_cad_m3"]
                        )
                        for p in PROVINCES
                    },
                    allocation,
                )
                current_cost = _weighted(
                    {
                        p: float(
                            current[(p, scenario, "base")]["total_cost_cad_m3"]
                        )
                        for p in PROVINCES
                    },
                    allocation,
                )
                province_name = "Canada"
                reason = (
                    "Volume-weighted diagnostic. The BC road and QC direct "
                    "cost upgrades change the weighted cost stack; the delta "
                    "is a model method change, not an observed market change."
                )
            else:
                before_margin = {
                    case: float(
                        before[(province, scenario, case)]["margin_cad_m3"]
                    )
                    for case in ("low", "base", "high")
                }
                current_margin = {
                    case: float(
                        current[(province, scenario, case)]["margin_cad_m3"]
                    )
                    for case in ("low", "base", "high")
                }
                before_cost = float(
                    before[(province, scenario, "base")]["total_cost_cad_m3"]
                )
                current_cost = float(
                    current[(province, scenario, "base")]["total_cost_cad_m3"]
                )
                province_name = PROVINCE_NAMES[province]
                if province == "BC":
                    reason = (
                        "BC road donor replaced by the published IAM 2026 "
                        "road-maintenance CAD/m3 allowance. The allowance is "
                        "maintenance-only; construction CAD/km was not "
                        "converted, so the change is partial direct coverage."
                    )
                elif province == "QC":
                    reason = (
                        "QC logging, haul and road replaced by MRNF 2022 T20 "
                        "survey aggregates. Road is gross before transfers and "
                        "low/high use the published regional range."
                    )
                elif province in AB_ON_NO_DIRECT_SERIES:
                    reason = (
                        "Source map indicates no direct public cost series. "
                        "Donor costs retained and no value invented; margin is "
                        "unchanged."
                    )
                else:
                    reason = (
                        "No direct public cost series is materialised. Donor "
                        "costs retained; margin is unchanged."
                    )
            source_keys = (
                f"{QC_SOURCE_KEY};{BC_ROAD_SOURCE_KEY};"
                f"{BC_COST_SOURCE_KEY};bc_log_market_2025_12"
            )
            rows.append(
                {
                    "row_type": "national" if province == "NATIONAL" else "province",
                    "price_scenario": scenario,
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "province": province,
                    "province_name": province_name,
                    "v2_1_margin_low_cad_m3": _fmt(before_margin["low"]),
                    "v2_1_margin_base_cad_m3": _fmt(before_margin["base"]),
                    "v2_1_margin_high_cad_m3": _fmt(before_margin["high"]),
                    "v2_2_margin_low_cad_m3": _fmt(current_margin["low"]),
                    "v2_2_margin_base_cad_m3": _fmt(current_margin["base"]),
                    "v2_2_margin_high_cad_m3": _fmt(current_margin["high"]),
                    "margin_low_delta_cad_m3": _fmt(
                        current_margin["low"] - before_margin["low"]
                    ),
                    "margin_base_delta_cad_m3": _fmt(
                        current_margin["base"] - before_margin["base"]
                    ),
                    "margin_high_delta_cad_m3": _fmt(
                        current_margin["high"] - before_margin["high"]
                    ),
                    "v2_1_total_cost_base_cad_m3": _fmt(before_cost),
                    "v2_2_total_cost_base_cad_m3": _fmt(current_cost),
                    "total_cost_base_delta_cad_m3": _fmt(
                        current_cost - before_cost
                    ),
                    "comparison_status": "model_method_change_not_observed",
                    "source_year": "2022;2025;2026",
                    "tier": "mixed_model_estimate",
                    "source_keys": source_keys,
                    "source": source_keys,
                    "source_urls": _source_urls(source_keys.split(";")),
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": reason,
                }
            )
    return rows


def _tier_before(province: str, component: str, price_tier: str) -> str:
    if component == "price":
        return price_tier
    if component in {"logging", "haul"}:
        return TIER_PROXY if province == "BC" else TIER_DONOR
    if component == "road":
        return TIER_BC_SILVICULTURE if province == "QC" else TIER_DONOR
    if component == "silviculture":
        return TIER_BC_SILVICULTURE if province == "BC" else TIER_DONOR
    raise NationalEconomicsV22Error(f"unknown coverage component: {component}")


def _tier_after(province: str, component: str, price_tier: str) -> str:
    if component == "price":
        return price_tier
    if component in {"logging", "haul"}:
        if province == "QC":
            return TIER_QC_SURVEY
        return TIER_PROXY if province == "BC" else TIER_DONOR
    if component == "road":
        if province == "QC":
            return TIER_QC_SURVEY
        return TIER_BC_APPRAISAL if province == "BC" else TIER_DONOR
    if component == "silviculture":
        return TIER_BC_SILVICULTURE if province == "BC" else TIER_DONOR
    raise NationalEconomicsV22Error(f"unknown coverage component: {component}")


DIRECT_TIERS = frozenset(
    {TIER_QC_SURVEY, TIER_BC_APPRAISAL, TIER_BC_SILVICULTURE}
)


def _coverage_rows(
    prices: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
) -> list[dict[str, Any]]:
    price_by_key = {
        (row["province"], row["price_scenario"]): row for row in prices
    }
    total_volume = sum(allocation.values())
    if total_volume <= 0.0:
        raise NationalEconomicsV22Error("allocation volume sum is zero")
    components = ("price", "logging", "haul", "road", "silviculture")
    cells: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for province in PROVINCES:
            price_tier = price_by_key[(province, scenario)]["price_tier"]
            volume_share = allocation[province] / total_volume
            for component in components:
                before = _tier_before(province, component, price_tier)
                after = _tier_after(province, component, price_tier)
                changed = before != after
                direct_before = before in DIRECT_TIERS
                direct_after = after in DIRECT_TIERS
                upgrade_kind = ""
                if changed and direct_after and not direct_before:
                    upgrade_kind = "direct_coverage_gain"
                elif changed:
                    upgrade_kind = "tier_relabelled_same_directness"
                cells.append(
                    {
                        "row_type": "coverage_cell",
                        "version": "v2_2",
                        "price_scenario": scenario,
                        "province": province,
                        "component": component,
                        "tier": after,
                        "tier_before": before,
                        "tier_after": after,
                        "tier_changed": str(changed).lower(),
                        "upgrade_kind": upgrade_kind,
                        "p1_priority": (
                            "P1"
                            if upgrade_kind == "direct_coverage_gain"
                            and volume_share >= 0.10
                            else ""
                        ),
                        "cell_count": "1",
                        "direct_cell_count_before": (
                            "1" if direct_before else "0"
                        ),
                        "direct_cell_count_after": "1" if direct_after else "0",
                        "direct_before": str(direct_before).lower(),
                        "direct_after": str(direct_after).lower(),
                        "volume_share": _fmt(volume_share, 8),
                        "volume_weighted_direct_share": "",
                        "scope": "all_components",
                        "evidence": EVIDENCE,
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                        "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                        "notes": (
                            "One province/component evidence cell. Direct "
                            "tiers are reported survey aggregates or published "
                            "appraisal allowances; donor and proxy tiers are "
                            "not direct."
                        ),
                    }
                )

    rows: list[dict[str, Any]] = list(cells)
    for scenario in SCENARIOS:
        scoped = [
            cell for cell in cells if cell["price_scenario"] == scenario
        ]
        for version, tier_field in (("v2_1", "tier_before"), ("v2_2", "tier_after")):
            counts = Counter(cell[tier_field] for cell in scoped)
            for tier, count in sorted(counts.items()):
                rows.append(
                    {
                        "row_type": "tier_count",
                        "version": version,
                        "price_scenario": scenario,
                        "province": "",
                        "component": "",
                        "tier": tier,
                        "tier_before": "",
                        "tier_after": "",
                        "tier_changed": "",
                        "upgrade_kind": "",
                        "p1_priority": "",
                        "cell_count": str(count),
                        "direct_cell_count_before": "",
                        "direct_cell_count_after": "",
                        "direct_before": "",
                        "direct_after": "",
                        "volume_share": "",
                        "volume_weighted_direct_share": "",
                        "scope": "all_components",
                        "evidence": EVIDENCE,
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                        "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                        "notes": f"{version} tier count across 65 coverage cells.",
                    }
                )
        for version, direct_field, scope, divisor in (
            ("v2_1", "direct_before", "all_components", 5),
            ("v2_2", "direct_after", "all_components", 5),
            ("v2_1", "direct_before", "cost_components", 4),
            ("v2_2", "direct_after", "cost_components", 4),
        ):
            pool = [
                cell
                for cell in scoped
                if scope == "all_components" or cell["component"] != "price"
            ]
            direct = [cell for cell in pool if cell[direct_field] == "true"]
            direct_before = [
                cell for cell in pool if cell["direct_before"] == "true"
            ]
            direct_after = [
                cell for cell in pool if cell["direct_after"] == "true"
            ]
            weighted = (
                sum(float(cell["volume_share"]) for cell in direct) / divisor
            )
            rows.append(
                {
                    "row_type": "coverage_summary",
                    "version": version,
                    "price_scenario": scenario,
                    "province": "",
                    "component": "",
                    "tier": "mixed",
                    "tier_before": "",
                    "tier_after": "",
                    "tier_changed": "",
                    "upgrade_kind": "",
                    "p1_priority": "",
                    "cell_count": str(len(pool)),
                    "direct_cell_count_before": str(len(direct_before)),
                    "direct_cell_count_after": str(len(direct_after)),
                    "direct_before": "",
                    "direct_after": "",
                    "volume_share": "",
                    "volume_weighted_direct_share": _fmt(weighted, 8),
                    "scope": scope,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": (
                        "Direct-cell count and volume-weighted direct share. "
                        "No full direct coverage is claimed."
                    ),
                }
            )
    upgrades = [
        cell
        for cell in cells
        if cell["price_scenario"] == BASE_SCENARIO
        and cell["upgrade_kind"] == "direct_coverage_gain"
    ]
    for cell in upgrades:
        rows.append(
            {
                "row_type": "p1_upgrade",
                "version": "v2_1_to_v2_2",
                "price_scenario": "both",
                "province": cell["province"],
                "component": cell["component"],
                "tier": cell["tier_after"],
                "tier_before": cell["tier_before"],
                "tier_after": cell["tier_after"],
                "tier_changed": "true",
                "upgrade_kind": cell["upgrade_kind"],
                "p1_priority": cell["p1_priority"],
                "cell_count": "1",
                "direct_cell_count_before": "0",
                "direct_cell_count_after": "1",
                "direct_before": "false",
                "direct_after": "true",
                "volume_share": cell["volume_share"],
                "volume_weighted_direct_share": "",
                "scope": "cost_components",
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "notes": (
                    "P1 direct-coverage upgrade. The cell was a donor value in "
                    "v2.1 and is a reported survey aggregate or published "
                    "appraisal allowance in v2.2."
                ),
            }
        )
    return rows


def _cost_input_rows(
    component_inputs: Mapping[str, Mapping[str, CostComponent]]
) -> list[dict[str, Any]]:
    order = {
        "harvest": 0,
        "haul": 1,
        "logging_haul": 1,
        "road": 2,
        "silviculture": 3,
    }
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        inputs = sorted(
            component_inputs[province].values(),
            key=lambda component: (order[component.component], component.component),
        )
        for component in inputs:
            if component.tier in DIRECT_TIERS:
                if province == "QC" and component.component == "road":
                    replacement_status = "direct_reused_relabelled"
                elif component.direct_series_status.startswith(
                    "direct_used_partial"
                ):
                    replacement_status = "direct_upgraded_partial"
                else:
                    replacement_status = "direct_upgraded"
            elif component.tier == TIER_PROXY:
                replacement_status = "proxy_model_retained"
            elif province in AB_ON_NO_DIRECT_SERIES:
                replacement_status = "no_direct_series_available"
            else:
                replacement_status = "donor_retained"
            rows.append(
                {
                    "row_type": "cost_component",
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "component": component.component,
                    "unit": "CAD/m3",
                    "low_cad_m3": _fmt(component.low_cad_m3),
                    "base_cad_m3": _fmt(component.base_cad_m3),
                    "high_cad_m3": _fmt(component.high_cad_m3),
                    "source_key": component.source_key,
                    "source": component.source,
                    "source_url": component.source_url,
                    "source_year": component.source_year,
                    "tier": component.tier,
                    "direct_series_status": component.direct_series_status,
                    "donor_province": component.donor_province,
                    "replacement_status": replacement_status,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": component.notes,
                }
            )
    return rows


def _assumption_rows(ratio: tuple[float, float, float]) -> list[dict[str, Any]]:
    rows = [
        {
            "assumption_id": "scenario_decision",
            "category": "scenario",
            "applies_to": "all",
            "rule": "donor_flat is the base scenario; stumpage_scaled is sensitivity",
            "value": "base=donor_flat;sensitivity=stumpage_scaled",
            "unit": "text",
            "source": "v2.1 decision retained in v2.2",
            "tier": "documented_constraint",
            "notes": (
                "The price method is unchanged from v2.1. v2.2 changes only "
                "cost inputs where direct public evidence exists."
            ),
        },
        {
            "assumption_id": "qc_direct_cost_upgrade",
            "category": "cost",
            "applies_to": "QC",
            "rule": "use MRNF 2022 T20 harvest, haul and road survey aggregates",
            "value": "31.42/24.76/16.58",
            "unit": "CAD/m3 base",
            "source": QC_SOURCE_KEY,
            "tier": TIER_QC_SURVEY,
            "notes": (
                "T20 before transfers; road gross; not T5 after-PRCCM. Survey "
                "aggregates, not observed transactions."
            ),
        },
        {
            "assumption_id": "qc_regional_range",
            "category": "cost",
            "applies_to": "QC",
            "rule": "low/high are the published regional SEPM minimum/maximum",
            "value": "regional_range",
            "unit": "method",
            "source": QC_SOURCE_KEY,
            "tier": TIER_QC_SURVEY,
            "notes": (
                "Seven regional means are used only for the range. R09 is a "
                "three-respondent mean and remains flagged in the source."
            ),
        },
        {
            "assumption_id": "qc_road_gross",
            "category": "cost",
            "applies_to": "QC",
            "rule": "do not combine T5 after-PRCCM road with T10 transfers",
            "value": "gross_access_cost",
            "unit": "text",
            "source": QC_SOURCE_KEY,
            "tier": "documented_constraint",
            "notes": (
                "T20 road is access_cost_gross. The T5 after-PRCCM total is "
                "not used because T10 transfers already include the rebate."
            ),
        },
        {
            "assumption_id": "bc_road_appraisal",
            "category": "cost",
            "applies_to": "BC",
            "rule": "use published IAM 2026 road-maintenance CAD/m3 allowance",
            "value": "median_road_group",
            "unit": "method",
            "source": BC_ROAD_SOURCE_KEY,
            "tier": TIER_BC_APPRAISAL,
            "notes": (
                "Low/base/high are the minimum/median/maximum of 22 published "
                "road maintenance groups. Appraisal allowance, not observed cost."
            ),
        },
        {
            "assumption_id": "bc_road_partial",
            "category": "cost",
            "applies_to": "BC",
            "rule": "do not convert road construction CAD/km or CAD/each to CAD/m3",
            "value": "partial_road_maintenance",
            "unit": "text",
            "source": BC_ROAD_SOURCE_KEY,
            "tier": "documented_constraint",
            "notes": (
                "The allowance covers road maintenance only. It is not a full "
                "road construction/amortization value and does not establish "
                "full direct BC road coverage."
            ),
        },
        {
            "assumption_id": "ab_on_no_direct_series",
            "category": "cost",
            "applies_to": "AB,ON",
            "rule": "keep donor costs and record missing direct cost series",
            "value": "donor_retained",
            "unit": "text",
            "source": "source map",
            "tier": "documented_constraint",
            "notes": (
                "No direct public logging, haul, road or silviculture cost "
                "series is materialised for Alberta or Ontario. No fake value "
                "is inserted."
            ),
        },
        {
            "assumption_id": "other_donors_retained",
            "category": "cost",
            "applies_to": "SK,MB,NB,NS,NL,PE,YT,NT,NU",
            "rule": "keep donor costs where no direct series is materialised",
            "value": "donor_retained",
            "unit": "text",
            "source": "source map",
            "tier": "documented_constraint",
            "notes": "No value is invented for jurisdictions without direct cost data.",
        },
        {
            "assumption_id": "margin_formula",
            "category": "margin",
            "applies_to": "all",
            "rule": "price proxy - logging - haul - road - silviculture",
            "value": "price - logging_haul - road - silviculture",
            "unit": "CAD/m3",
            "source": "this model",
            "tier": "model_estimate",
            "notes": "Low uses high cost; high uses low cost.",
        },
        {
            "assumption_id": "negative_margins_unclipped",
            "category": "margin",
            "applies_to": "all",
            "rule": "negative margins are retained and labelled",
            "value": "unclipped",
            "unit": "boolean",
            "source": "this model",
            "tier": "documented_constraint",
            "notes": "No floor at zero.",
        },
        {
            "assumption_id": "national_weighting",
            "category": "aggregation",
            "applies_to": "national",
            "rule": "volume-weight provincial margins by the 119 Mt allocation",
            "value": "119000000",
            "unit": "m3_case",
            "source": "provincial_allocation_119m",
            "tier": "model_estimate",
            "notes": "Diagnostic only; model_estimate_not_policy.",
        },
        {
            "assumption_id": "labels",
            "category": "label",
            "applies_to": "all",
            "rule": "all rows model_estimate, observed=false, policy_grade=false",
            "value": "false",
            "unit": "boolean",
            "source": "this model",
            "tier": "documented_constraint",
            "notes": (
                "Reported survey aggregates and appraisal allowances are "
                "model inputs, not observed transactions."
            ),
        },
        {
            "assumption_id": "price_year_mismatch",
            "category": "price",
            "applies_to": "all",
            "rule": "no deflator applied between 2025 BC price, 2022 QC costs and 2026 allowances",
            "value": "1.0000",
            "unit": "index",
            "source": "this model",
            "tier": "assumption",
            "notes": "Nominal mismatch is explicit and not corrected.",
        },
        {
            "assumption_id": "dues_not_comparable",
            "category": "comparison",
            "applies_to": "all",
            "rule": "dues/stumpage rates are not delivered prices",
            "value": "not_comparable",
            "unit": "text",
            "source": "this model",
            "tier": "documented_constraint",
            "notes": v2_1.DUES_NOT_COMPARABLE,
        },
    ]
    for row in rows:
        row.update(
            {
                "base_scenario": BASE_SCENARIO,
                "sensitivity_scenario": SENSITIVITY_SCENARIO,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            }
        )
    return rows


def _source_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in v2.build_sources():
        row = dict(row)
        row.update(
            {
                "source_year": str(row.get("publication_date", ""))[:4] or "2026",
                "base_scenario": BASE_SCENARIO,
                "sensitivity_scenario": SENSITIVITY_SCENARIO,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
            }
        )
        rows.append(row)
    rows.append(
        {
            "source_key": BC_ROAD_SOURCE_KEY,
            "title": BC_APPRAISAL_SOURCE_TITLE,
            "publisher": "BC Timber Pricing Branch, Ministry of Forests",
            "publication_date": "2026-07-01",
            "source_year": "2026",
            "retrieved_utc": v2.RETRIEVED_UTC,
            "url": BC_APPRAISAL_SOURCE_URL,
            "sha256": _sha256(BC_APPRAISAL_CSV),
            "licence": "Public-reference BC Crown copyright; not strict-open.",
            "evidence_tier": TIER_BC_APPRAISAL,
            "strict_open": STRICT_OPEN,
            "used_for": "BC road-maintenance CAD/m3 allowance.",
            "redistributed": "false",
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "evidence": EVIDENCE,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
        }
    )
    rows.append(
        {
            "source_key": "national_economics_v2_1",
            "title": "National economics v2.1 donor-flat base",
            "publisher": "This repository",
            "publication_date": "2026-11-22",
            "source_year": "2026",
            "retrieved_utc": v2.RETRIEVED_UTC,
            "url": "data/derived/national/harvest_basis/national_economics_v2_1",
            "sha256": _sha256(V21_DIR / MARGIN_CSV),
            "licence": "CC0 1.0 Universal public-domain dedication for this model.",
            "evidence_tier": EVIDENCE,
            "strict_open": STRICT_OPEN,
            "used_for": "Before-version margin comparison.",
            "redistributed": "false",
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "evidence": EVIDENCE,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
        }
    )
    return rows


def _find_cost_row(
    rows: Sequence[Mapping[str, Any]], province: str, component: str
) -> Mapping[str, Any]:
    matches = [
        row
        for row in rows
        if row["province"] == province and row["component"] == component
    ]
    if len(matches) != 1:
        raise NationalEconomicsV22Error(
            f"expected one cost row for {province}/{component}, found {len(matches)}"
        )
    return matches[0]


def _assert_labels(rows: Sequence[Mapping[str, Any]], context: str) -> None:
    for row in rows:
        if row["evidence"] != EVIDENCE:
            raise NationalEconomicsV22Error(f"{context} evidence label: {row}")
        if row["observed"] != OBSERVED:
            raise NationalEconomicsV22Error(f"{context} observed label: {row}")
        if row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsV22Error(f"{context} policy label: {row}")
        if "not_estimated" in " ".join(str(value) for value in row.values()).lower():
            raise NationalEconomicsV22Error(f"{context} not-estimated marker: {row}")


def _validate(
    cost_rows: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, Any]],
) -> None:
    if len(cost_rows) != 40:
        raise NationalEconomicsV22Error(
            f"expected 40 cost input rows, found {len(cost_rows)}"
        )
    _assert_labels(cost_rows, "cost input")
    for row in cost_rows:
        if row["unit"] != "CAD/m3":
            raise NationalEconomicsV22Error(f"cost unit violation: {row}")
        _require_ordered(
            float(row["low_cad_m3"]),
            float(row["base_cad_m3"]),
            float(row["high_cad_m3"]),
            f"{row['province']}/{row['component']} cost input",
        )

    qc_harvest = _find_cost_row(cost_rows, "QC", "harvest")
    qc_haul = _find_cost_row(cost_rows, "QC", "haul")
    qc_road = _find_cost_row(cost_rows, "QC", "road")
    for row, expected in (
        (qc_harvest, (28.27, 31.42, 34.48)),
        (qc_haul, (19.32, 24.76, 36.86)),
        (qc_road, (7.47, 16.58, 22.13)),
    ):
        actual = (
            float(row["low_cad_m3"]),
            float(row["base_cad_m3"]),
            float(row["high_cad_m3"]),
        )
        if any(
            not math.isclose(a, b, abs_tol=1e-6)
            for a, b in zip(actual, expected, strict=True)
        ):
            raise NationalEconomicsV22Error(
                f"unexpected QC direct cost for {row['component']}: {actual}"
            )
        if row["tier"] != TIER_QC_SURVEY:
            raise NationalEconomicsV22Error(f"QC tier violation: {row}")
        if row["source_year"] != "2022":
            raise NationalEconomicsV22Error(f"QC source year violation: {row}")
        if "not observed" not in row["notes"].lower():
            raise NationalEconomicsV22Error(f"QC not-observed note missing: {row}")
    if math.isclose(float(qc_road["low_cad_m3"]), 15.52, abs_tol=1e-6):
        raise NationalEconomicsV22Error("QC road low uses after-PRCCM T5 value")

    bc_road = _find_cost_row(cost_rows, "BC", "road")
    if (
        bc_road["tier"] != TIER_BC_APPRAISAL
        or bc_road["source_key"] != BC_ROAD_SOURCE_KEY
    ):
        raise NationalEconomicsV22Error(f"BC road tier/source violation: {bc_road}")
    if not (
        math.isclose(float(bc_road["low_cad_m3"]), 1.00, abs_tol=1e-6)
        and math.isclose(float(bc_road["base_cad_m3"]), 5.93, abs_tol=1e-6)
        and math.isclose(float(bc_road["high_cad_m3"]), 9.78, abs_tol=1e-6)
    ):
        raise NationalEconomicsV22Error(f"unexpected BC road allowance: {bc_road}")

    for province in AB_ON_NO_DIRECT_SERIES:
        for component in ("logging_haul", "road", "silviculture"):
            row = _find_cost_row(cost_rows, province, component)
            if row["direct_series_status"] != "not_available_direct_cost_series":
                raise NationalEconomicsV22Error(
                    f"{province} direct-series status violation: {row}"
                )
            if row["tier"] != TIER_DONOR:
                raise NationalEconomicsV22Error(f"{province} donor tier violation: {row}")
            if row["replacement_status"] != "no_direct_series_available":
                raise NationalEconomicsV22Error(
                    f"{province} replacement status violation: {row}"
                )
            if "no value invented" not in row["notes"].lower():
                raise NationalEconomicsV22Error(
                    f"{province} missing no-invention note: {row}"
                )

    if len(margins) != len(PROVINCES) * len(SCENARIOS) * 3:
        raise NationalEconomicsV22Error(
            f"expected {len(PROVINCES) * len(SCENARIOS) * 3} margin rows, "
            f"found {len(margins)}"
        )
    _assert_labels(margins, "margin")
    by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    for scenario in SCENARIOS:
        provinces = {
            row["province"] for row in margins if row["price_scenario"] == scenario
        }
        if provinces != set(PROVINCES):
            raise NationalEconomicsV22Error(
                f"{scenario} missing provinces: "
                f"{sorted(set(PROVINCES) - provinces)}"
            )
        for province in PROVINCES:
            for case in ("low", "base", "high"):
                row = by_key[(province, scenario, case)]
                margin = float(row["margin_cad_m3"])
                total = float(row["total_cost_cad_m3"])
                parts = (
                    float(row["logging_haul_cad_m3"])
                    + float(row["road_cad_m3"])
                    + float(row["silviculture_cad_m3"])
                )
                if not math.isclose(margin, float(row["price_cad_m3"]) - parts, abs_tol=2e-3):
                    raise NationalEconomicsV22Error(
                        f"margin arithmetic violation: {province}/{scenario}/{case}"
                    )
                if not math.isclose(total, parts, abs_tol=2e-3):
                    raise NationalEconomicsV22Error(
                        f"total cost arithmetic violation: {province}/{scenario}/{case}"
                    )
                if row["negative_margin_clipped"] != "false":
                    raise NationalEconomicsV22Error(f"clipped margin: {row}")
            low = by_key[(province, scenario, "low")]
            base = by_key[(province, scenario, "base")]
            high = by_key[(province, scenario, "high")]
            if not (
                float(low["margin_cad_m3"])
                <= float(base["margin_cad_m3"])
                <= float(high["margin_cad_m3"])
            ):
                raise NationalEconomicsV22Error(
                    f"unordered margins: {province}/{scenario}"
                )
        qc_central = by_key[("QC", scenario, "base")]
        if not (
            math.isclose(float(qc_central["logging_haul_cad_m3"]), 56.18, abs_tol=1e-6)
            and math.isclose(float(qc_central["road_cad_m3"]), 16.58, abs_tol=1e-6)
        ):
            raise NationalEconomicsV22Error(f"unexpected QC central costs: {qc_central}")
        bc_central = by_key[("BC", scenario, "base")]
        if not math.isclose(float(bc_central["road_cad_m3"]), 5.93, abs_tol=1e-6):
            raise NationalEconomicsV22Error(f"unexpected BC central road: {bc_central}")

    if len(summary_rows) != len(PROVINCES) * len(SCENARIOS) + len(SCENARIOS):
        raise NationalEconomicsV22Error("summary row count mismatch")
    _assert_labels(summary_rows, "summary")
    for row in summary_rows:
        if row["base_scenario"] != BASE_SCENARIO:
            raise NationalEconomicsV22Error(f"summary base scenario: {row}")
        if row["row_type"] == "national":
            if row["national_diagnostic_label"] != NATIONAL_DIAGNOSTIC_LABEL:
                raise NationalEconomicsV22Error("national diagnostic label missing")
        elif not (
            float(row["margin_low_cad_m3"])
            <= float(row["margin_base_cad_m3"])
            <= float(row["margin_high_cad_m3"])
        ):
            raise NationalEconomicsV22Error(
                f"unordered summary margins: {row['province']}/{row['price_scenario']}"
            )

    if len(prices) != len(PROVINCES) * len(SCENARIOS):
        raise NationalEconomicsV22Error("price row count mismatch")
    _assert_labels(prices, "price")

    if len(comparison) != (len(PROVINCES) + 1) * len(SCENARIOS):
        raise NationalEconomicsV22Error("comparison row count mismatch")
    _assert_labels(comparison, "comparison")
    comparison_by_key = {
        (row["province"], row["price_scenario"]): row for row in comparison
    }
    for province in AB_ON_NO_DIRECT_SERIES:
        for scenario in SCENARIOS:
            row = comparison_by_key[(province, scenario)]
            if not math.isclose(
                float(row["margin_base_delta_cad_m3"]), 0.0, abs_tol=1e-6
            ):
                raise NationalEconomicsV22Error(
                    f"{province} margin changed without direct cost data: {row}"
                )

    _assert_labels(tiers, "tier coverage")
    upgrades = [row for row in tiers if row["row_type"] == "p1_upgrade"]
    upgrade_cells = {(row["province"], row["component"]) for row in upgrades}
    if upgrade_cells != {("BC", "road"), ("QC", "logging"), ("QC", "haul")}:
        raise NationalEconomicsV22Error(f"unexpected P1 upgrades: {upgrade_cells}")
    if any(row["upgrade_kind"] == "direct_coverage_gain" for row in upgrades) is False:
        raise NationalEconomicsV22Error("P1 upgrade kind missing")

    base_before = next(
        row
        for row in tiers
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == BASE_SCENARIO
        and row["version"] == "v2_1"
        and row["scope"] == "all_components"
    )
    base_after = next(
        row
        for row in tiers
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == BASE_SCENARIO
        and row["version"] == "v2_2"
        and row["scope"] == "all_components"
    )
    if (
        base_before["direct_cell_count_before"] != "3"
        or base_after["direct_cell_count_after"] != "6"
    ):
        raise NationalEconomicsV22Error("unexpected direct coverage counts")
    if not (
        math.isclose(
            float(base_before["volume_weighted_direct_share"]),
            0.1792,
            abs_tol=5e-4,
        )
        and math.isclose(
            float(base_after["volume_weighted_direct_share"]),
            0.3360,
            abs_tol=5e-4,
        )
    ):
        raise NationalEconomicsV22Error("unexpected volume-weighted coverage share")

    negatives = [row for row in margins if float(row["margin_cad_m3"]) < 0.0]
    if not negatives:
        raise NationalEconomicsV22Error("expected documented negative margins")


def _write_report(
    out_dir: Path,
    summary: Mapping[str, Any],
    cost_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, Any]],
) -> None:
    national = {
        row["price_scenario"]: row
        for row in summary_rows
        if row["row_type"] == "national"
    }
    provinces = [row for row in summary_rows if row["row_type"] == "province"]
    by_province = {
        (row["province"], row["price_scenario"]): row for row in provinces
    }
    negatives = [row for row in margins if float(row["margin_cad_m3"]) < 0.0]
    negative_by_scenario = Counter(row["price_scenario"] for row in negatives)
    base_negative_central = [
        row
        for row in negatives
        if row["price_scenario"] == BASE_SCENARIO
        and row["scenario_id"] == "base"
    ]
    scaled_negative_central = [
        row
        for row in negatives
        if row["price_scenario"] == SENSITIVITY_SCENARIO
        and row["scenario_id"] == "base"
    ]
    lines = [
        "# National Economics v2.2 - direct P1 cost inputs where available",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        "## What changed",
        "",
        (
            "Version 2.2 keeps the v2.1 price scenarios unchanged and upgrades "
            "only the cost cells with published direct evidence. The base "
            "price scenario remains `donor_flat`; `stumpage_scaled` remains a "
            "sensitivity."
        ),
        "",
        "## QC cost upgrade",
        "",
        (
            "Quebec harvest, haul and road now use the MRNF 2022 T20 survey "
            "synthesis: **harvest 31.42**, **haul 24.76**, **road 16.58 "
            "CAD/m3**. Road is `access_cost_gross` before PRCCM; the T5 "
            "after-PRCCM value is deliberately not used with T10 transfers."
        ),
        "",
        "| Component | Low | Base | High | Tier | Source year |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for component in ("harvest", "haul", "road"):
        row = _find_cost_row(cost_rows, "QC", component)
        lines.append(
            f"| QC {component} | {row['low_cad_m3']} | {row['base_cad_m3']} | "
            f"{row['high_cad_m3']} | `{row['tier']}` | {row['source_year']} |"
        )
    lines += [
        "",
        (
            "Low/high are the published regional SEPM minimum/maximum across "
            "the seven regional means. R09 is a three-respondent mean and "
            "remains flagged in the source. These are survey aggregates, not "
            "observed transactions."
        ),
        "",
        "## BC road result",
        "",
        (
            "A published BC road-management CAD/m3 allowance exists: the IAM "
            "2026 Table 4-6 road-maintenance groups. It is used with "
            "low/base/high equal to the minimum/median/maximum of the 22 "
            "published groups."
        ),
        "",
        "| Low | Base | High | Tier | Source year |",
        "|---:|---:|---:|---|---:|",
    ]
    bc_road = _find_cost_row(cost_rows, "BC", "road")
    lines += [
        (
            f"| {bc_road['low_cad_m3']} | {bc_road['base_cad_m3']} | "
            f"{bc_road['high_cad_m3']} | `{bc_road['tier']}` | "
            f"{bc_road['source_year']} |"
        ),
        "",
        (
            "This is a **maintenance-only** appraisal allowance. BC road "
            "construction is published as CAD/km and was **not** converted to "
            "CAD/m3. The result is partial direct road coverage, not a full "
            "road construction/amortization cost."
        ),
        "",
        "## AB and ON",
        "",
        (
            "Alberta and Ontario keep the donor cost values. The source map "
            "indicates no direct public logging, haul, road or silviculture "
            "cost series for either jurisdiction. The rows are labelled "
            "`not_available_direct_cost_series`; no value is invented and "
            "their central margins are unchanged from v2.1."
        ),
        "",
        "## Margins and coverage delta",
        "",
        "| Province | Base donor_flat low | Base donor_flat central | Base donor_flat high | Sensitivity scaled central |",
        "|---|---:|---:|---:|---:|",
    ]
    for province in PROVINCES:
        base = by_province[(province, BASE_SCENARIO)]
        scaled = by_province[(province, SENSITIVITY_SCENARIO)]
        lines.append(
            f"| {province} | {base['margin_low_cad_m3']} | "
            f"{base['margin_base_cad_m3']} | {base['margin_high_cad_m3']} | "
            f"{scaled['margin_base_cad_m3']} |"
        )
    lines += [
        "",
        "| Scenario | Role | Low | Base | High | Label | Combined net value |",
        "|---|---|---:|---:|---:|---|---|",
        (
            f"| donor_flat | base | {national[BASE_SCENARIO]['margin_low_cad_m3']} | "
            f"{national[BASE_SCENARIO]['margin_base_cad_m3']} | "
            f"{national[BASE_SCENARIO]['margin_high_cad_m3']} | "
            f"`{NATIONAL_DIAGNOSTIC_LABEL}` | false |"
        ),
        (
            f"| stumpage_scaled | sensitivity | "
            f"{national[SENSITIVITY_SCENARIO]['margin_low_cad_m3']} | "
            f"{national[SENSITIVITY_SCENARIO]['margin_base_cad_m3']} | "
            f"{national[SENSITIVITY_SCENARIO]['margin_high_cad_m3']} | "
            f"`{NATIONAL_DIAGNOSTIC_LABEL}` | false |"
        ),
        "",
        (
            "The national numbers are volume-weighted diagnostics across all "
            "13 jurisdictions on the 119 Mt screening allocation. They are "
            "not a combined national net value, not a policy result and not a "
            "total of observed profits."
        ),
        "",
        "### Direct-cost coverage before and after",
        "",
        "| Scope | Version | Direct cells | Total cells | Volume-weighted direct share |",
        "|---|---|---:|---:|---:|",
    ]
    for scope_label, scope in (("All 5 components", "all_components"), ("Cost components only", "cost_components")):
        for version, direct_key, weight_key in (
            (
                "v2_1",
                "direct_cell_count_before",
                "volume_weighted_direct_share",
            ),
            (
                "v2_2",
                "direct_cell_count_after",
                "volume_weighted_direct_share",
            ),
        ):
            row = next(
                item
                for item in tiers
                if item["row_type"] == "coverage_summary"
                and item["price_scenario"] == BASE_SCENARIO
                and item["version"] == version
                and item["scope"] == scope
            )
            lines.append(
                f"| {scope_label} | {version} | "
                f"{row[direct_key]} | {row['cell_count']} | "
                f"{row[weight_key]} |"
            )
    lines += [
        "",
        (
            f"P1 direct-coverage upgrades: **{len(summary['direct_cost_coverage']['p1_upgrades'])}** "
            "cells (BC road, QC logging, QC haul). The QC road cell was "
            "already direct in v2.1 and is relabelled to the more precise "
            "`reported_survey_aggregate` tier; it is not counted as a new "
            "direct cell. Coverage is still partial: no full direct coverage "
            "is claimed."
        ),
        "",
        "### Scenario comparison",
        "",
        (
            "`v2_1_v2_2_comparison.csv` reports low/base/high margins and "
            "central total cost for v2.1 and v2.2 in both price scenarios. "
            "The deltas are model method changes, not observed market changes."
        ),
        "",
        "## Negative margins and labels",
        "",
        (
            f"{len(negatives)} of {len(margins)} margin rows are negative and "
            "are **unclipped**. Negatives are a result, not an error."
        ),
        (
            f"- `donor_flat`: {negative_by_scenario[BASE_SCENARIO]} negative rows, "
            f"{len(base_negative_central)} in the central case."
        ),
        (
            f"- `stumpage_scaled`: "
            f"{negative_by_scenario[SENSITIVITY_SCENARIO]} negative rows, "
            f"{len(scaled_negative_central)} in the central case."
        ),
        "",
        (
            "Every row carries `evidence=model_estimate`, `observed=false`, "
            "`policy_grade=false` and `combined_national_net_value=false`."
        ),
        "",
        "## Non-claims",
        "",
        "- Not observed profit, not a mill margin and not official net value.",
        "- Survey aggregates and appraisal allowances are model inputs, not transactions.",
        "- BC road is maintenance-only and does not establish full road cost coverage.",
        "- AB and ON have no direct cost series; donor values remain.",
        "- No combined national net value is computed.",
        "- No strict-open licence claim is made; source documents are not redistributed.",
        "- No restricted, confidential or microdata input is used.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python -m workflows.national.harvest.national_economics_v2_2",
        "```",
    ]
    (out_dir / REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(
    out_dir: Path,
    outputs: Sequence[Path],
    summary: Mapping[str, Any],
) -> None:
    inputs = []
    for path in (
        V21_DIR / MARGIN_CSV,
        V21_DIR / SUMMARY_JSON,
        V1_DIR / "summary.json",
        V1_DIR / "national_margin_estimates.csv",
        QC_SURVEY_CSV,
        QC_SURVEY_MANIFEST,
        BC_APPRAISAL_CSV,
        BC_APPRAISAL_MANIFEST,
        v1.BC_LOG_MARKET_PRICES_CSV,
        v1.BC_COST_ESTIMATES_CSV,
        ALLOCATION_CSV,
        v1.SLOPE_OVERLAY_CSV,
        v1.ROAD_DISTANCE_CSV,
    ):
        if not path.is_file():
            raise NationalEconomicsV22Error(f"missing manifest input: {path}")
        if any(marker in str(path).lower() for marker in RESTRICTED_MARKERS):
            raise NationalEconomicsV22Error(f"restricted input path: {path}")
        inputs.append(
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": _sha256(path),
            }
        )
    external_sources = [
        {
            "source_key": source.key,
            "url": source.url,
            "sha256": source.sha256,
            "retrieved_utc": v2.RETRIEVED_UTC,
            "redistributed": False,
        }
        for source in v2.SOURCES.values()
        if not source.url.startswith("data/")
    ]
    external_sources.append(
        {
            "source_key": BC_ROAD_SOURCE_KEY,
            "url": BC_APPRAISAL_SOURCE_URL,
            "sha256": _sha256(BC_APPRAISAL_CSV),
            "retrieved_utc": v2.RETRIEVED_UTC,
            "redistributed": False,
        }
    )
    manifest = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "observation_type": EVIDENCE,
        "base_scenario": BASE_SCENARIO,
        "sensitivity_scenario": SENSITIVITY_SCENARIO,
        "stumpage_scaled_observed": False,
        "combined_national_net_value": False,
        "restricted_inputs_used": [],
        "labels": {
            "observed": False,
            "policy_grade": False,
            "combined_national_net_value": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
        },
        "qc_cost_upgrade": summary["qc_cost_upgrade"],
        "bc_road": summary["bc_road"],
        "ab_on": summary["ab_on"],
        "direct_cost_coverage": summary["direct_cost_coverage"],
        "inputs": inputs,
        "external_sources": external_sources,
        "outputs": [
            {"path": path.name, "sha256": _sha256(path)} for path in outputs
        ],
    }
    (out_dir / MANIFEST_JSON).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def build(out_dir: Path = DEFAULT_OUT_DIR, *, check_deletions: bool = True) -> dict[str, Any]:
    if check_deletions:
        assert_no_unexpected_deletions()
    out_dir.mkdir(parents=True, exist_ok=True)
    blend = v1.load_bc_price_blend()
    slope = v1.load_slope_context()
    road = v1.load_road_context()
    road_allowance = v1.load_road_allowance()
    silviculture = v1.load_silviculture_allowance()
    costs = v1.build_cost_proxies(
        v1.load_bc_cost_distribution(),
        slope,
        road,
        road_allowance,
        silviculture,
        v1.load_qc_evidence(),
    )
    allocation = v1.load_allocation_volumes()
    volume_reference = v2._volume_reference()
    refs = v2._references()
    for province in PROVINCES:
        reference = refs[province]
        if reference.extracted:
            assert reference.rate_low_cad_m3 is not None
            assert reference.rate_base_cad_m3 is not None
            assert reference.rate_high_cad_m3 is not None
            if not (
                0.0
                < reference.rate_low_cad_m3
                <= reference.rate_base_cad_m3
                <= reference.rate_high_cad_m3
            ):
                raise NationalEconomicsV22Error(
                    f"invalid stumpage range for {province}: {reference}"
                )
        elif reference.rate_type != v2.RATE_TYPE_FALLBACK:
            raise NationalEconomicsV22Error(f"{province} missing fallback label")
    ratio = v2._ratio(blend)
    if not 0.0 < ratio[0] <= ratio[1] <= ratio[2]:
        raise NationalEconomicsV22Error(f"invalid BC scaled ratio: {ratio}")

    qc = _load_qc_direct_costs()
    bc_road, cam_road_management = _load_bc_road_allowance()
    bc_silviculture, donor_silviculture = _silviculture_components()
    component_inputs = _build_component_inputs(
        costs, qc, bc_road, bc_silviculture, donor_silviculture
    )
    adjusted_costs = _apply_cost_inputs(costs, component_inputs)

    price_rows = _price_rows(v2.build_prices(refs, blend, ratio))
    margin_rows = _margin_rows(
        v2.build_margins(price_rows, adjusted_costs, allocation, refs),
        price_rows,
        refs,
        component_inputs,
    )
    summary_rows = _summary_rows(
        margin_rows,
        price_rows,
        refs,
        allocation,
        volume_reference,
        blend,
        ratio,
        adjusted_costs,
        component_inputs,
    )
    comparison_rows = _comparison_rows(margin_rows, allocation)
    tier_rows = _coverage_rows(price_rows, allocation)
    cost_rows = _cost_input_rows(component_inputs)
    assumption_rows = _assumption_rows(ratio)
    source_rows = _source_rows()

    _validate(
        cost_rows,
        margin_rows,
        summary_rows,
        price_rows,
        comparison_rows,
        tier_rows,
    )

    files = {
        COST_INPUTS_CSV: cost_rows,
        MARGIN_CSV: margin_rows,
        SUMMARY_CSV: summary_rows,
        COMPARISON_CSV: comparison_rows,
        TIER_CSV: tier_rows,
        ASSUMPTIONS_CSV: assumption_rows,
        SOURCES_CSV: source_rows,
    }
    for filename, rows in files.items():
        _write_csv(out_dir / filename, rows)

    negatives = [row for row in margin_rows if float(row["margin_cad_m3"]) < 0.0]
    negative_by_scenario = {
        scenario: sum(
            1 for row in negatives if row["price_scenario"] == scenario
        )
        for scenario in SCENARIOS
    }
    negative_central = {
        scenario: sum(
            1
            for row in negatives
            if row["price_scenario"] == scenario and row["scenario_id"] == "base"
        )
        for scenario in SCENARIOS
    }
    national = {
        row["price_scenario"]: row
        for row in summary_rows
        if row["row_type"] == "national"
    }
    p1_upgrades = [
        {
            "province": row["province"],
            "component": row["component"],
            "tier_before": row["tier_before"],
            "tier_after": row["tier_after"],
            "volume_share": row["volume_share"],
        }
        for row in tier_rows
        if row["row_type"] == "p1_upgrade"
    ]
    base_before = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == BASE_SCENARIO
        and row["version"] == "v2_1"
        and row["scope"] == "all_components"
    )
    base_after = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == BASE_SCENARIO
        and row["version"] == "v2_2"
        and row["scope"] == "all_components"
    )
    cost_before = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == BASE_SCENARIO
        and row["version"] == "v2_1"
        and row["scope"] == "cost_components"
    )
    cost_after = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == BASE_SCENARIO
        and row["version"] == "v2_2"
        and row["scope"] == "cost_components"
    )
    generated = datetime.now(UTC).replace(microsecond=0).isoformat()
    summary = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "model_estimate_direct_p1_cost_inputs_partial",
        "description": (
            "13-jurisdiction margins with donor_flat as the base scenario and "
            "stumpage_scaled as the sensitivity; QC T20 direct costs and the "
            "BC IAM road-maintenance allowance replace donor values where "
            "published. Coverage remains partial."
        ),
        "decision": {
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "stumpage_scaled_observed": False,
            "stumpage_scaled_policy_grade": False,
            "reason": (
                "The v2.1 price decision is retained. v2.2 upgrades only cost "
                "cells with published direct evidence and keeps donor values "
                "elsewhere."
            ),
        },
        "qc_cost_upgrade": {
            "province": "QC",
            "components": {
                "harvest": {
                    "low_cad_m3": qc["harvest"].low_cad_m3,
                    "base_cad_m3": qc["harvest"].base_cad_m3,
                    "high_cad_m3": qc["harvest"].high_cad_m3,
                    "tier": qc["harvest"].tier,
                    "source_year": qc["harvest"].source_year,
                },
                "haul": {
                    "low_cad_m3": qc["haul"].low_cad_m3,
                    "base_cad_m3": qc["haul"].base_cad_m3,
                    "high_cad_m3": qc["haul"].high_cad_m3,
                    "tier": qc["haul"].tier,
                    "source_year": qc["haul"].source_year,
                },
                "road": {
                    "low_cad_m3": qc["road"].low_cad_m3,
                    "base_cad_m3": qc["road"].base_cad_m3,
                    "high_cad_m3": qc["road"].high_cad_m3,
                    "tier": qc["road"].tier,
                    "source_year": qc["road"].source_year,
                },
            },
            "basis": (
                "MRNF 2022 T20 SEPM synthesis before transfers; road gross; "
                "regional min/max range; not observed transaction"
            ),
            "observed_transaction": False,
        },
        "bc_road": {
            "province": "BC",
            "low_cad_m3": bc_road.low_cad_m3,
            "base_cad_m3": bc_road.base_cad_m3,
            "high_cad_m3": bc_road.high_cad_m3,
            "tier": bc_road.tier,
            "source_year": bc_road.source_year,
            "method": "IAM 2026 Table 4-6 road maintenance min/median/max",
            "partial": True,
            "cam_road_management_crosscheck_cad_m3": cam_road_management,
            "construction_converted": False,
            "observed_cost": False,
        },
        "ab_on": {
            "provinces": sorted(AB_ON_NO_DIRECT_SERIES),
            "direct_series_status": "not_available_direct_cost_series",
            "donor_retained": True,
            "values_invented": False,
        },
        "direct_cost_coverage": {
            "before": {
                "all_components": {
                    "direct_cells": int(base_before["direct_cell_count_before"]),
                    "total_cells": int(base_before["cell_count"]),
                    "volume_weighted_direct_share": float(
                        base_before["volume_weighted_direct_share"]
                    ),
                },
                "cost_components": {
                    "direct_cells": int(cost_before["direct_cell_count_before"]),
                    "total_cells": int(cost_before["cell_count"]),
                    "volume_weighted_direct_share": float(
                        cost_before["volume_weighted_direct_share"]
                    ),
                },
            },
            "after": {
                "all_components": {
                    "direct_cells": int(base_after["direct_cell_count_after"]),
                    "total_cells": int(base_after["cell_count"]),
                    "volume_weighted_direct_share": float(
                        base_after["volume_weighted_direct_share"]
                    ),
                },
                "cost_components": {
                    "direct_cells": int(cost_after["direct_cell_count_after"]),
                    "total_cells": int(cost_after["cell_count"]),
                    "volume_weighted_direct_share": float(
                        cost_after["volume_weighted_direct_share"]
                    ),
                },
            },
            "p1_upgrades": p1_upgrades,
            "full_direct_coverage_claimed": False,
        },
        "coverage": {
            "provinces": list(PROVINCES),
            "province_count": len(PROVINCES),
            "missing": [],
            "not_estimated_count": 0,
            "cost_input_rows": len(cost_rows),
            "margin_rows": len(margin_rows),
            "summary_rows": len(summary_rows),
            "price_rows": len(price_rows),
            "comparison_rows": len(comparison_rows),
            "tier_rows": len(tier_rows),
            "scenario_count": len(SCENARIOS),
            "scenarios": list(SCENARIOS),
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "nu_zero_allocation_volume": allocation["NU"] == 0.0,
        },
        "national_diagnostic": {
            "allocation_volume_m3": float(
                national[BASE_SCENARIO]["allocation_volume_m3"]
            ),
            "base_scenario": BASE_SCENARIO,
            "donor_flat": {
                "margin_low_cad_m3": float(
                    national[BASE_SCENARIO]["margin_low_cad_m3"]
                ),
                "margin_base_cad_m3": float(
                    national[BASE_SCENARIO]["margin_base_cad_m3"]
                ),
                "margin_high_cad_m3": float(
                    national[BASE_SCENARIO]["margin_high_cad_m3"]
                ),
            },
            "stumpage_scaled_sensitivity": {
                "margin_low_cad_m3": float(
                    national[SENSITIVITY_SCENARIO]["margin_low_cad_m3"]
                ),
                "margin_base_cad_m3": float(
                    national[SENSITIVITY_SCENARIO]["margin_base_cad_m3"]
                ),
                "margin_high_cad_m3": float(
                    national[SENSITIVITY_SCENARIO]["margin_high_cad_m3"]
                ),
            },
            "label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": False,
        },
        "margins": {
            "negative_row_count": len(negatives),
            "negative_by_scenario": negative_by_scenario,
            "negative_central_case_count": negative_central,
            "negative_documented": True,
            "negative_margin_clipped": False,
        },
        "labels": {
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "combined_national_net_value": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "strict_open": False,
        },
        "licensing": v2.LICENSING,
        "validation": {
            "all_provinces_present": True,
            "all_rows_model_estimate": True,
            "observed_claim": False,
            "policy_grade_claim": False,
            "strict_open_claim": False,
            "low_base_high_ordered": True,
            "negatives_unclipped": True,
            "dues_not_comparable_to_delivered_price": True,
            "combined_national_net_value_computed": False,
            "qc_direct_costs_used": True,
            "qc_after_prccm_road_not_used": True,
            "bc_road_appraisal_allowance_used": True,
            "bc_road_construction_converted": False,
            "ab_on_donor_retained": True,
            "full_direct_coverage_claimed": False,
        },
        "source_retrieved_utc": v2.RETRIEVED_UTC,
        "summary_rows": summary_rows,
    }
    (out_dir / SUMMARY_JSON).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(
        out_dir, summary, cost_rows, summary_rows, margin_rows, tier_rows
    )
    output_paths = [out_dir / name for name in files] + [
        out_dir / SUMMARY_JSON,
        out_dir / REPORT_MD,
    ]
    _write_manifest(out_dir, output_paths, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--skip-deletion-check",
        action="store_true",
        help="skip the fail-closed git deletion preflight",
    )
    args = parser.parse_args(argv)
    summary = build(args.out, check_deletions=not args.skip_deletion_check)
    print(
        json.dumps(
            {
                "out_dir": str(args.out),
                "coverage": summary["coverage"],
                "national_diagnostic": summary["national_diagnostic"],
                "direct_cost_coverage": summary["direct_cost_coverage"],
                "negative_margins": summary["margins"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



