"""Build national facility capacity v3 from public AB/ON/QC mill evidence.

v3 starts from capacity v2 and adds explicit published mill capacities:

* Quebec MRNF 2026 permitted primary-processing mills, where the permitted
  softwood plus hardwood annual consumption is an input-volume capacity;
* per-mill company disclosures for four Ontario sawmills (GreenFirst) and one
  Alberta sawmill (Tolko High Level), expressed as sawn-lumber output and
  converted to m3 at the international 2.36 m3 per thousand board feet factor.

Quebec plants are matched one-to-one to inventory v1 by province, facility
class, normalised name, and distance.  Unmatched Quebec mills are added as new
records.  Every other blank relevant-class record keeps the v2 BC class-median
proxy.  Existing published capacities are never overwritten.

All values remain model inputs: ``model_estimate`` / ``observed=false`` /
``policy_grade=false``.  No legal or AAC claim is made.  No restricted data are
used and no UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_capacity_v2 as v2,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v1"
    / "national_facilities.csv"
)
CAPACITY_V2_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v2"
    / "national_facility_capacity_v2.csv"
)
CAPACITY_V2_SUMMARY = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v2"
    / "summary.json"
)
EXTRACT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/public_mill_capacity_2026"
)
QC_CSV = EXTRACT_DIR / "qc_mrnf_usines_2026.csv"
ON_MILLS_CSV = EXTRACT_DIR / "on_mnrf_mills_2026.csv"
COMPANY_CSV = EXTRACT_DIR / "company_mill_capacity_2025.csv"
EXTRACT_SOURCES = EXTRACT_DIR / "sources.json"
OUTPUT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_facility_capacity_v3"
)

ARTIFACT = "national_facility_capacity_v3"
ARTIFACT_VERSION = "3.0.0"
QC_SOURCE_ID = "qc_mrnf_usines_2026"
QC_SOURCE_URL = (
    "https://www.donneesquebec.ca/recherche/dataset/"
    "usines-de-transformation-primaire-du-bois"
)
QC_LICENCE = "Attribution 4.0 International (CC BY 4.0)"
QC_CAPACITY_YEAR = 2026
BOARD_FEET_TO_M3 = 2360.0

PUBLISHED_AUTHORIZED = "published_authorized_consumption"
PUBLISHED_SAWN_OUTPUT = "published_sawn_output_converted"
PUBLISHED_LABELS = frozenset(
    {
        v2.PUBLISHED_INFEED_EVIDENCE,
        v2.PUBLISHED_CONVERTED_EVIDENCE,
        PUBLISHED_AUTHORIZED,
        PUBLISHED_SAWN_OUTPUT,
    }
)

CAPACITY_FIELDS = [
    *v2.CAPACITY_FIELDS,
    "capacity_year",
    "capacity_source_id",
    "capacity_source_url",
    "capacity_licence",
    "capacity_location",
]

GENERIC_NAME_TOKENS = frozenset(
    {
        "scierie",
        "sawmill",
        "sawmills",
        "mill",
        "mills",
        "usine",
        "bois",
        "forest",
        "forests",
        "forestier",
        "forestiers",
        "forestieres",
        "products",
        "produits",
        "inc",
        "ltee",
        "ltd",
        "ulc",
        "corp",
        "corporation",
        "company",
        "compagnie",
        "division",
        "energie",
        "energy",
        "group",
        "groupe",
        "les",
        "de",
        "du",
        "des",
        "la",
        "le",
        "et",
        "and",
        "of",
        "the",
        "sciages",
    }
)


class CapacityV3Error(v2.CapacityV2Error):
    """Raised when a capacity v3 invariant fails."""


@dataclass(frozen=True, slots=True)
class PublishedMill:
    """One explicit published mill-capacity observation."""

    source_row: int
    name: str
    city: str
    province: str
    facility_class: str
    lat: float
    lon: float
    capacity_m3_y: float
    evidence: str
    basis: str
    year: int
    source_id: str
    source_url: str
    licence: str
    location: str
    notes: str
    plant_id: str


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = decomposed.encode("ascii", "ignore").decode()
    without_parentheses = re.sub(r"\(.*?\)", " ", ascii_text.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_parentheses).split())


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _normalise(text).split()
        if len(token) >= 3 and token not in GENERIC_NAME_TOKENS
    }


def _haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    return 6371.0 * 2.0 * math.asin(
        math.sqrt(
            math.sin((lat2 - lat1) / 2.0) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2.0) ** 2
        )
    )


def _queclass(row: dict[str, str]) -> str:
    category = row["catcomplet"]
    product = row["produits"].lower()
    if category.startswith("2"):
        return "SAW"
    if category.startswith(("1", "8")):
        return "PULP"
    if category.startswith(("3", "4")):
        return "PANEL"
    if category.startswith("6"):
        if "granul" in product:
            return "PELLET"
        return "BIO"
    return "OTHER"


def _class_compatibility(first: str, second: str) -> int:
    if first == second:
        return 2
    if {first, second} <= {"BIO", "PELLET"}:
        return 1
    if {first, second} <= {"PULP", "BIO"}:
        return 1
    if {first, second} <= {"PANEL", "SAW"}:
        return 0
    return -2


def load_qc_mills(path: Path = QC_CSV) -> list[PublishedMill]:
    rows = v2.v1._read_csv(path)
    required = {
        "nosequsi",
        "catcomplet",
        "usicomplet",
        "muncomplet",
        "produits",
        "volresper",
        "volfeuper",
        "latitude",
        "longitude",
    }
    v2.v1._require_columns(rows, required, path)
    mills: list[PublishedMill] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=2):
        plant_id = row["nosequsi"].strip()
        if not plant_id:
            raise CapacityV3Error(f"{path}:{index}: empty plant id")
        if plant_id in seen:
            raise CapacityV3Error(f"{path}:{index}: duplicate plant id {plant_id}")
        seen.add(plant_id)
        capacity = float(row["volresper"] or 0.0) + float(
            row["volfeuper"] or 0.0
        )
        if capacity <= 0:
            # Category-8 paper mills appear in the register without a
            # permitted wood volume.  They are real mills but carry no
            # capacity evidence, so they are excluded rather than given a
            # fabricated value.
            continue
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        if not (44.0 <= lat <= 63.0 and -80.0 <= lon <= -57.0):
            raise CapacityV3Error(
                f"{path}:{index}: coordinates outside Quebec: {lat}, {lon}"
            )
        facility_class = _queclass(row)
        city = row["muncomplet"].split(" (")[0].strip()
        mills.append(
            PublishedMill(
                source_row=index,
                name=row["usicomplet"].strip(),
                city=city,
                province="QC",
                facility_class=facility_class,
                lat=lat,
                lon=lon,
                capacity_m3_y=capacity,
                evidence=PUBLISHED_AUTHORIZED,
                basis="published_authorized_consumption_m3_y",
                year=QC_CAPACITY_YEAR,
                source_id=QC_SOURCE_ID,
                source_url=QC_SOURCE_URL,
                licence=QC_LICENCE,
                location=city,
                notes=(
                    f"Quebec permit {plant_id}; category {row['catcomplet']}; "
                    f"product {row['produits']}; permitted softwood "
                    f"{row['volresper'] or 0} m3/yr plus hardwood "
                    f"{row['volfeuper'] or 0} m3/yr. Permit ceiling, not "
                    "observed throughput."
                ),
                plant_id=plant_id,
            )
        )
    return mills


def load_company_mills(path: Path = COMPANY_CSV) -> list[PublishedMill]:
    rows = v2.v1._read_csv(path)
    required = {
        "company",
        "mill_name",
        "city",
        "province",
        "lat",
        "lon",
        "capacity_value",
        "capacity_unit",
        "capacity_m3_y",
        "capacity_metric",
        "capacity_year",
        "source_id",
        "source_url",
        "licence",
        "source_title",
    }
    v2.v1._require_columns(rows, required, path)
    mills: list[PublishedMill] = []
    for index, row in enumerate(rows, start=2):
        province = row["province"].strip()
        if province not in {"ON", "AB"}:
            raise CapacityV3Error(
                f"{path}:{index}: company extract outside AB/ON: {province}"
            )
        if row["capacity_unit"].strip() != "MMfbm":
            raise CapacityV3Error(
                f"{path}:{index}: unsupported unit {row['capacity_unit']!r}"
            )
        if row["capacity_metric"].strip() != "sawn_lumber_output":
            raise CapacityV3Error(
                f"{path}:{index}: unsupported metric {row['capacity_metric']!r}"
            )
        capacity = float(row["capacity_m3_y"])
        expected = float(row["capacity_value"]) * BOARD_FEET_TO_M3
        if abs(capacity - expected) > 1e-9:
            raise CapacityV3Error(
                f"{path}:{index}: converted capacity {capacity} != "
                f"{expected} for {row['capacity_value']} MMfbm"
            )
        facility_class = "SAW"
        mills.append(
            PublishedMill(
                source_row=index,
                name=row["mill_name"].strip(),
                city=row["city"].strip(),
                province=province,
                facility_class=facility_class,
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                capacity_m3_y=capacity,
                evidence=PUBLISHED_SAWN_OUTPUT,
                basis="published_sawn_output_converted_2_36_m3_per_mfbm",
                year=int(row["capacity_year"]),
                source_id=row["source_id"].strip(),
                source_url=row["source_url"].strip(),
                licence=row["licence"].strip(),
                location=row["city"].strip(),
                notes=(
                    f"{row['source_title']}: {row['company']} reports "
                    f"{row['capacity_value']} million board feet of annual "
                    "sawn-lumber capacity; converted to m3 sawn output at "
                    "2.36 m3 per thousand board feet. Output basis, not log "
                    "intake."
                ),
                plant_id=f"{province}-COMPANY-{index - 1}",
            )
        )
    return mills


def _capacity_row(record: dict[str, str]) -> dict[str, Any]:
    return {
        field: record.get(field, "")
        for field in CAPACITY_FIELDS
        if field in record
    }


def _published_row(
    mill: PublishedMill, existing: dict[str, Any] | None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "facility_id": (
            existing["facility_id"]
            if existing is not None
            else f"PUB26-{mill.province}-{mill.plant_id}"
        ),
        "name": mill.name if existing is None else existing["name"] or mill.name,
        "facility_class": (
            mill.facility_class if existing is None else existing["facility_class"]
        ),
        "province": mill.province,
        "lat": f"{mill.lat:.6f}",
        "lon": f"{mill.lon:.6f}",
        "capacity_m3_y": mill.capacity_m3_y,
        "capacity_status": v2.PUBLISHED,
        "capacity_evidence": mill.evidence,
        "proxy_low_m3_y": "",
        "proxy_high_m3_y": "",
        "anchor_class": "",
        "anchor_records": 0,
        "province_scale": 1.0,
        "source_id": mill.source_id,
        "source_url": mill.source_url,
        "licence": mill.licence,
        "evidence_tier": "A",
        "status": "operating",
        "notes": mill.notes,
        "capacity_basis": mill.basis,
        "conversion_density_t_per_m3": "",
        "capacity_year": mill.year,
        "capacity_source_id": mill.source_id,
        "capacity_source_url": mill.source_url,
        "capacity_licence": mill.licence,
        "capacity_location": mill.location,
    }
    if existing is None:
        return row
    row["notes"] = (
        f"{existing.get('notes', '')} Published capacity added by v3 from "
        f"{mill.source_id} ({mill.year}); {mill.notes}"
    ).strip()
    return row


def _is_relevant(record: dict[str, Any]) -> bool:
    return record["facility_class"] in v2.v1.RELEVANT_CLASSES


def _match(
    mills: Sequence[PublishedMill],
    records: Sequence[dict[str, Any]],
) -> tuple[dict[int, int], list[int], list[int]]:
    """Return mill->record index, unmatched mill indices, unmatched record indices."""

    candidates: list[tuple[float, int, int]] = []
    for mill_index, mill in enumerate(mills):
        mill_tokens = _tokens(mill.name)
        for record_index, record in enumerate(records):
            if record["province"] != mill.province:
                continue
            if record["capacity_status"] == v2.PUBLISHED:
                continue
            try:
                distance = _haversine_km(
                    (mill.lat, mill.lon),
                    (float(record["lat"]), float(record["lon"])),
                )
            except (TypeError, ValueError):
                continue
            if distance > 30.0:
                continue
            compatibility = _class_compatibility(
                mill.facility_class, record["facility_class"]
            )
            record_tokens = _tokens(record["name"])
            name_score = 0
            if mill_tokens and record_tokens:
                if _normalise(record["name"]) == _normalise(mill.name):
                    name_score = 10
                elif mill_tokens <= record_tokens or record_tokens <= mill_tokens:
                    name_score = 6
                elif mill_tokens & record_tokens:
                    name_score = 3
            distance_score = (
                5.0
                if distance <= 2.0
                else 4.0
                if distance <= 5.0
                else 2.0
                if distance <= 10.0
                else 1.0
                if distance <= 20.0
                else 0.5
            )
            accepted = (
                (name_score >= 10 and compatibility >= 0)
                or (name_score >= 3 and distance <= 15.0 and compatibility >= 0)
                or (distance <= 3.0 and compatibility >= 1)
                or (distance <= 8.0 and compatibility >= 1 and name_score >= 3)
            )
            if not accepted:
                continue
            score = compatibility + name_score + distance_score
            if score <= 0:
                continue
            candidates.append((score, mill_index, record_index))
    candidates.sort(reverse=True)
    matched_mills: set[int] = set()
    matched_records: set[int] = set()
    assignment: dict[int, int] = {}
    for _, mill_index, record_index in candidates:
        if mill_index in matched_mills or record_index in matched_records:
            continue
        matched_mills.add(mill_index)
        matched_records.add(record_index)
        assignment[mill_index] = record_index
    unmatched_mills = [
        index for index in range(len(mills)) if index not in matched_mills
    ]
    unmatched_records = [
        index for index in range(len(records)) if index not in matched_records
    ]
    return assignment, unmatched_mills, unmatched_records


def _summary(
    *,
    generated_utc: str,
    rows: Sequence[dict[str, Any]],
    qc_rows: int,
    qc_excluded_no_volume: int,
    company_rows: int,
    matched_qc: int,
    new_qc: int,
    matched_company: int,
    new_company: int,
    sources: Sequence[dict[str, str]],
    class_medians: Sequence[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    counts = {
        "records_total": len(rows),
        "published_records": sum(
            row["capacity_status"] == v2.PUBLISHED for row in rows
        ),
        "proxy_records": sum(row["capacity_status"] == v2.PROXY for row in rows),
        "not_published_records": sum(
            row["capacity_status"] == v2.NOT_PUBLISHED for row in rows
        ),
    }
    counts["records_by_class"] = dict(
        sorted(
            (
                facility_class,
                sum(row["facility_class"] == facility_class for row in rows),
            )
            for facility_class in v2.v1.CLASS_ORDER
        )
    )
    counts["records_by_province"] = dict(
        sorted(
            (
                province,
                sum(row["province"] == province for row in rows),
            )
            for province in v2.v1.PROVINCES
        )
    )
    total_capacity = sum(float(row["capacity_m3_y"] or 0.0) for row in rows)
    published_capacity = sum(
        float(row["capacity_m3_y"] or 0.0)
        for row in rows
        if row["capacity_status"] == v2.PUBLISHED
    )
    proxy_capacity = sum(
        float(row["capacity_m3_y"] or 0.0)
        for row in rows
        if row["capacity_status"] == v2.PROXY
    )
    capacity: dict[str, Any] = {
        "total_capacity_m3_y": total_capacity,
        "published_capacity_m3_y": published_capacity,
        "proxy_capacity_m3_y": proxy_capacity,
        "published_share_of_total": (
            published_capacity / total_capacity if total_capacity else None
        ),
        "proxy_share_of_total": (
            proxy_capacity / total_capacity if total_capacity else None
        ),
        "published_share_of_records": (
            counts["published_records"] / counts["records_total"]
        ),
        "proxy_share_of_records": counts["proxy_records"] / counts["records_total"],
    }
    by_class: dict[str, dict[str, Any]] = {}
    by_province: dict[str, dict[str, Any]] = {}
    for facility_class in v2.v1.CLASS_ORDER:
        group = [row for row in rows if row["facility_class"] == facility_class]
        by_class[facility_class] = {
            "records": len(group),
            "published": sum(
                row["capacity_status"] == v2.PUBLISHED for row in group
            ),
            "published_capacity_m3_y": sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in group
                if row["capacity_status"] == v2.PUBLISHED
            ),
            "proxy": sum(row["capacity_status"] == v2.PROXY for row in group),
            "proxy_capacity_m3_y": sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in group
                if row["capacity_status"] == v2.PROXY
            ),
            "unavailable": sum(
                row["capacity_status"] == v2.NOT_PUBLISHED for row in group
            ),
        }
    for province in v2.v1.PROVINCES:
        group = [row for row in rows if row["province"] == province]
        by_province[province] = {
            "records": len(group),
            "published": sum(
                row["capacity_status"] == v2.PUBLISHED for row in group
            ),
            "published_capacity_m3_y": sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in group
                if row["capacity_status"] == v2.PUBLISHED
            ),
            "proxy": sum(row["capacity_status"] == v2.PROXY for row in group),
            "proxy_capacity_m3_y": sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in group
                if row["capacity_status"] == v2.PROXY
            ),
            "unavailable": sum(
                row["capacity_status"] == v2.NOT_PUBLISHED for row in group
            ),
            "total_capacity_m3_y": sum(
                float(row["capacity_m3_y"] or 0.0) for row in group
            ),
        }
    capacity["by_class"] = by_class
    capacity["by_province"] = by_province
    evidence_counts = {
        label: {
            "records": sum(row["capacity_evidence"] == label for row in rows),
            "capacity_m3_y": sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in rows
                if row["capacity_evidence"] == label
            ),
        }
        for label in sorted(PUBLISHED_LABELS | {v2.PROXY_EVIDENCE})
    }
    capacity["evidence"] = evidence_counts

    before = baseline.get("counts", {})
    before_capacity = baseline.get("capacity", {})
    coverage = {
        "before": {
            **{key: before.get(key) for key in counts if key in before},
            "published_capacity_m3_y": before_capacity.get(
                "published_capacity_m3_y"
            ),
            "proxy_capacity_m3_y": before_capacity.get("proxy_capacity_m3_y"),
            "total_capacity_m3_y": before_capacity.get("total_capacity_m3_y"),
        },
        "after": {
            **counts,
            "published_capacity_m3_y": published_capacity,
            "proxy_capacity_m3_y": proxy_capacity,
            "total_capacity_m3_y": total_capacity,
            "published_share_of_records": (
                counts["published_records"] / counts["records_total"]
            ),
            "proxy_share_of_records": (
                counts["proxy_records"] / counts["records_total"]
            ),
        },
    }
    before_total = float(before_capacity.get("total_capacity_m3_y") or 0.0)
    coverage["before"]["published_share_of_records"] = (
        before.get("published_records", 0) / before["records_total"]
        if before.get("records_total")
        else None
    )
    coverage["before"]["proxy_share_of_records"] = (
        before.get("proxy_records", 0) / before["records_total"]
        if before.get("records_total")
        else None
    )
    coverage["before"]["published_share_of_capacity"] = (
        float(before_capacity.get("published_capacity_m3_y") or 0.0)
        / before_total
        if before_total
        else None
    )
    coverage["before"]["proxy_share_of_capacity"] = (
        float(before_capacity.get("proxy_capacity_m3_y") or 0.0) / before_total
        if before_total
        else None
    )
    coverage["after"]["published_share_of_capacity"] = capacity[
        "published_share_of_total"
    ]
    coverage["after"]["proxy_share_of_capacity"] = capacity["proxy_share_of_total"]

    match = {
        "qc_source_rows": qc_rows,
        "qc_excluded_no_volume": qc_excluded_no_volume,
        "qc_matched_existing": matched_qc,
        "qc_new_records": new_qc,
        "company_source_rows": company_rows,
        "company_matched_existing": matched_company,
        "company_new_records": new_company,
        "matched_total": matched_qc + matched_company,
        "new_total": new_qc + new_company,
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": v2.EVIDENCE_LABEL,
            "observed": v2.OBSERVED,
            "policy_grade": v2.POLICY_GRADE,
        },
        "counts": counts,
        "capacity": capacity,
        "coverage": coverage,
        "match": match,
        "class_medians": list(class_medians),
        "method": {
            "inventory": "national_facility_inventory_v1",
            "base": "national_facility_capacity_v2",
            "qc_capacity_rule": (
                "permitted annual softwood plus hardwood consumption "
                "(volresper + volfeuper) from the June 2026 Quebec register"
            ),
            "qc_match_rule": (
                "one-to-one same-province match by normalised name, facility "
                "class, and distance; no record receives two source mills"
            ),
            "new_record_rule": (
                "unmatched relevant-class Quebec mills are added; no unit is "
                "fabricated and no closed mill is added"
            ),
            "company_rule": (
                "per-mill company disclosures in million board feet converted "
                "to m3 sawn output at 2.36 m3 per thousand board feet"
            ),
            "proxy_rule": (
                "every blank relevant-class record without published evidence "
                "keeps the v2 BC class-median proxy"
            ),
            "published_precedence": (
                "existing published capacities are preserved; a matched "
                "source never overwrites them"
            ),
            "capacity_units": (
                "m3/yr mixed evidence: 2015 infeed proxy, converted finished-"
                "pellet bulk volume, Quebec permitted input volume, and "
                "converted sawn-lumber output"
            ),
        },
        "sources": list(sources),
        "validation": {
            "unique_facility_ids": len({row["facility_id"] for row in rows})
            == len(rows),
            "all_published_rows_have_accepted_evidence": all(
                row["capacity_evidence"] in PUBLISHED_LABELS
                for row in rows
                if row["capacity_status"] == v2.PUBLISHED
            ),
            "all_proxy_rows_have_class_median_evidence": all(
                row["capacity_evidence"] == v2.PROXY_EVIDENCE
                for row in rows
                if row["capacity_status"] == v2.PROXY
            ),
            "all_capacities_non_negative": all(
                float(row["capacity_m3_y"] or 0.0) >= 0.0 for row in rows
            ),
            "all_new_rows_are_published": all(
                row["capacity_status"] == v2.PUBLISHED
                for row in rows
                if row["facility_id"].startswith("PUB26-")
            ),
            "record_conservation": len(rows)
            == baseline["counts"]["records_total"] + new_qc + new_company,
            "no_restricted_sources": not any(
                marker in (source["licence"] + " " + source["notes"]).lower()
                for source in sources
                for marker in (
                    "proprietary",
                    "restricted",
                    "confidential",
                    "subscription",
                )
            ),
        },
        "caveats": [
            (
                "All values are model estimates (`model_estimate`); "
                "`observed=false` and `policy_grade=false`."
            ),
            (
                "Capacity remains mixed-basis: BC 2015 infeed proxies, "
                "converted 2025 finished-pellet bulk volume, Quebec 2026 "
                "permitted input volume, and converted company sawn-lumber "
                "output. The bases are not interchangeable."
            ),
            (
                "Quebec permitted consumption is a regulatory ceiling, not "
                "observed throughput, and a permit does not prove current "
                "operation."
            ),
            (
                "The Ontario and Alberta company capacities are sawn-lumber "
                "output converted at 2.36 m3/Mfbm. They are not log-intake "
                "equivalents and understate the roundwood a sawmill can "
                "consume."
            ),
            (
                "Ontario's public 2026 mill list has no facility capacity; it "
                "is used only for names, types, cities, and coordinates. "
                "Alberta's public mill directory is historical and was not "
                "used for current capacity."
            ),
            (
                "Class-median proxies remain statistical placeholders for "
                "blank relevant-class records, not facility measurements."
            ),
            (
                "No legal/AAC determination, timber-supply approval, or "
                "observed operating capacity is made."
            ),
            (
                "No restricted or proprietary data are used. No UI or public "
                "OSeCAN surface is changed."
            ),
        ],
        "inputs": {},
    }
    return summary


def _report(summary: dict[str, Any]) -> str:
    before = summary["coverage"]["before"]
    after = summary["coverage"]["after"]
    match = summary["match"]
    lines = [
        "# National Facility Capacity V3",
        "",
        (
            "Capacity v2 plus Quebec MRNF 2026 permitted mill volumes and five "
            "per-mill Ontario/Alberta company disclosures. All values are "
            "model estimates: `observed=false`, `policy_grade=false`."
        ),
        "",
        "## Extraction and match",
        "",
        (
            f"- Quebec source rows: "
            f"{match['qc_source_rows'] + match['qc_excluded_no_volume']} "
            f"({match['qc_excluded_no_volume']} category-8 rows without a "
            "volume excluded)"
        ),
        f"- Quebec matched to inventory v1: {match['qc_matched_existing']}",
        f"- Quebec new records: {match['qc_new_records']}",
        f"- Company source rows: {match['company_source_rows']}",
        f"- Company matched to inventory v1: {match['company_matched_existing']}",
        f"- Company new records: {match['company_new_records']}",
        "",
        "## Coverage v2 to v3",
        "",
        "| Measure | Capacity v2 | Capacity v3 | Delta |",
        "|---|---|---|---|",
    ]
    metrics = [
        ("Records", "records_total", 0),
        ("Published records", "published_records", 0),
        ("Proxy records", "proxy_records", 0),
        ("Not published records", "not_published_records", 0),
        ("Published capacity m3/yr", "published_capacity_m3_y", 0),
        ("Proxy capacity m3/yr", "proxy_capacity_m3_y", 0),
        ("Total capacity m3/yr", "total_capacity_m3_y", 0),
    ]
    for label, key, digits in metrics:
        old = before.get(key)
        new = after.get(key)
        delta = None if old is None or new is None else new - old
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    f"{old:,.0f}" if digits == 0 and old is not None else str(old),
                    f"{new:,.0f}" if digits == 0 and new is not None else str(new),
                    f"{delta:,.0f}"
                    if digits == 0 and delta is not None
                    else str(delta),
                ]
            )
            + " |"
        )
    for label, key in (
        ("Published share of records", "published_share_of_records"),
        ("Proxy share of records", "proxy_share_of_records"),
        ("Published share of capacity", "published_share_of_capacity"),
        ("Proxy share of capacity", "proxy_share_of_capacity"),
    ):
        old = before.get(key)
        new = after.get(key)
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    f"{old:.1%}" if old is not None else "",
                    f"{new:.1%}" if new is not None else "",
                    f"{new - old:+.1%}"
                    if old is not None and new is not None
                    else "",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Published evidence mix", ""])
    lines.append("| Evidence | Records | Capacity m3/yr |")
    lines.append("|---|---:|---:|")
    for label, block in summary["capacity"]["evidence"].items():
        lines.append(
            f"| {label} | {block['records']} | "
            f"{block['capacity_m3_y']:,.0f} |"
        )
    lines.extend(["", "## Validation", ""])
    for key, value in summary["validation"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Caveats", ""])
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


def _sources(path: Path = EXTRACT_SOURCES) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    fields = v2.SOURCE_FIELDS
    sources = [{field: row.get(field, "") for field in fields} for row in payload]
    sources.append(
        {
            "source_id": "class_median_proxy_v3",
            "title": "Class-median capacity proxy carried from capacity v2",
            "publisher": "UBC FRESH",
            "source_url": (
                "data/derived/national/facilities/"
                "national_facility_capacity_v2/class_medians.csv"
            ),
            "licence": (
                "Model-derived proxy. Anchors inherit the BC Mills DB / FFCM "
                "lineage; not a facility measurement and not relicensed."
            ),
            "licence_url": "",
            "access_method": "Carried from national_facility_capacity_v2",
            "retrieved_utc": "",
            "coverage": "Blank relevant-class records without published evidence",
            "access_status": "computed",
            "used_for_capacity": "yes_proxy",
            "notes": (
                "BC published infeed-proxy class medians; p25-p75 reported; "
                "no provincial scaling."
            ),
        }
    )
    sources.append(
        {
            "source_id": "bc_mills_db_2015",
            "title": "BC facility layer (FFCM / ISED Mills DB 2015 proxies)",
            "publisher": "UBC FRESH, derived from ISED Mills DB 2015 and FFCM",
            "source_url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "licence": (
                "Project-derived BC chain layer; upstream ISED Mills DB 2015 / "
                "FFCM proxies. Upstream source is not relicensed here."
            ),
            "licence_url": "",
            "access_method": "Carried from national_facility_capacity_v2",
            "retrieved_utc": "",
            "coverage": "British Columbia published infeed-proxy records",
            "access_status": "available",
            "used_for_capacity": "yes_published_and_anchor",
            "notes": "2015 processing-line infeed proxies, not licensed capacity.",
        }
    )
    return sources


def build(
    output_dir: Path = OUTPUT_DIR,
    inventory_path: Path = INVENTORY_CSV,
    capacity_v2_path: Path = CAPACITY_V2_CSV,
    capacity_v2_summary_path: Path = CAPACITY_V2_SUMMARY,
    qc_path: Path = QC_CSV,
    company_path: Path = COMPANY_CSV,
) -> dict[str, Any]:
    """Build capacity v3 and return its summary."""

    generated_utc = v2.v1._utc_now()
    inventory_rows = v2.v1._read_csv(inventory_path)
    v2.v1._require_columns(inventory_rows, {"facility_id"}, inventory_path)
    base_rows = [
        _capacity_row(row) for row in v2.v1._read_csv(capacity_v2_path)
    ]
    if len(base_rows) != len(inventory_rows):
        raise CapacityV3Error(
            f"{capacity_v2_path}: {len(base_rows)} rows vs "
            f"{len(inventory_rows)} inventory rows"
        )
    by_id = {row["facility_id"]: row for row in base_rows}
    if len(by_id) != len(base_rows):
        raise CapacityV3Error(f"{capacity_v2_path}: duplicate facility_id")

    with capacity_v2_summary_path.open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    class_medians = baseline.get("class_medians", [])
    medians = {row["facility_class"]: row for row in class_medians}

    qc_mills = load_qc_mills(qc_path)
    company_mills = load_company_mills(company_path)
    qc_assignment, qc_unmatched, _ = _match(qc_mills, base_rows)
    company_assignment, company_unmatched, _ = _match(company_mills, base_rows)

    for mill_index, record_index in qc_assignment.items():
        by_id[base_rows[record_index]["facility_id"]] = _published_row(
            qc_mills[mill_index], base_rows[record_index]
        )
    for mill_index, record_index in company_assignment.items():
        by_id[base_rows[record_index]["facility_id"]] = _published_row(
            company_mills[mill_index], base_rows[record_index]
        )
    new_rows: list[dict[str, Any]] = []
    for mill_index in qc_unmatched:
        mill = qc_mills[mill_index]
        if mill.facility_class == "OTHER":
            continue
        new_rows.append(_published_row(mill, None))
    for mill_index in company_unmatched:
        new_rows.append(_published_row(company_mills[mill_index], None))

    rows = [by_id[key] for key in by_id] + new_rows
    for row in rows:
        if row["capacity_status"] in {v2.PUBLISHED, v2.PROXY}:
            continue
        if not _is_relevant(row):
            row["capacity_status"] = v2.NOT_PUBLISHED
            row["capacity_m3_y"] = ""
            continue
        median = medians.get(row["facility_class"])
        if median is None:
            row["capacity_status"] = v2.NOT_PUBLISHED
            row["capacity_m3_y"] = ""
            continue
        row["capacity_status"] = v2.PROXY
        row["capacity_evidence"] = v2.PROXY_EVIDENCE
        row["capacity_m3_y"] = median["median_m3_y"]
        row["proxy_low_m3_y"] = median["p25_m3_y"]
        row["proxy_high_m3_y"] = median["p75_m3_y"]
        row["anchor_class"] = row["facility_class"]
        row["anchor_records"] = median["anchor_records"]
        row["province_scale"] = 1.0
        row["capacity_basis"] = "class_median_proxy_bc_anchor"
        row["capacity_year"] = ""
        row["capacity_source_id"] = v2.PROXY_SOURCE_ID
        row["capacity_source_url"] = ""
        row["capacity_licence"] = ""
        row["capacity_location"] = ""

    sources = _sources()
    summary = _summary(
        generated_utc=generated_utc,
        rows=rows,
        qc_rows=len(qc_mills),
        qc_excluded_no_volume=len(v2.v1._read_csv(qc_path)) - len(qc_mills),
        company_rows=len(company_mills),
        matched_qc=len(qc_assignment),
        new_qc=sum(
            1
            for index in qc_unmatched
            if qc_mills[index].facility_class != "OTHER"
        ),
        matched_company=len(company_assignment),
        new_company=len(company_unmatched),
        sources=sources,
        class_medians=class_medians,
        baseline=baseline,
    )
    summary["inputs"] = {
        str(path.relative_to(REPO_ROOT)): v2.v1._sha256(path)
        for path in (
            inventory_path,
            capacity_v2_path,
            capacity_v2_summary_path,
            qc_path,
            company_path,
            ON_MILLS_CSV,
            EXTRACT_SOURCES,
        )
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    v2.v1._write_csv(
        output_dir / "national_facility_capacity_v3.csv",
        CAPACITY_FIELDS,
        rows,
    )
    v2.v1._write_csv(
        output_dir / "sources.csv", v2.SOURCE_FIELDS, sources
    )
    v2.v1._write_csv(
        output_dir / "class_medians.csv",
        v2.CLASS_MEDIAN_FIELDS,
        class_medians,
    )
    province_class_rows = []
    for province in v2.v1.PROVINCES:
        for facility_class in v2.v1.CLASS_ORDER:
            group = [
                row
                for row in rows
                if row["province"] == province
                and row["facility_class"] == facility_class
            ]
            if not group:
                continue
            published_capacity = sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in group
                if row["capacity_status"] == v2.PUBLISHED
            )
            proxy_capacity = sum(
                float(row["capacity_m3_y"] or 0.0)
                for row in group
                if row["capacity_status"] == v2.PROXY
            )
            total = published_capacity + proxy_capacity
            province_class_rows.append(
                {
                    "province": province,
                    "facility_class": facility_class,
                    "records": len(group),
                    "published_records": sum(
                        row["capacity_status"] == v2.PUBLISHED for row in group
                    ),
                    "published_capacity_m3_y": published_capacity,
                    "proxy_records": sum(
                        row["capacity_status"] == v2.PROXY for row in group
                    ),
                    "proxy_capacity_m3_y": proxy_capacity,
                    "unavailable_records": sum(
                        row["capacity_status"] == v2.NOT_PUBLISHED
                        for row in group
                    ),
                    "total_capacity_m3_y": total,
                    "capacity_coverage_share": (
                        total / summary["capacity"]["total_capacity_m3_y"]
                        if summary["capacity"]["total_capacity_m3_y"]
                        else 0.0
                    ),
                }
            )
    v2.v1._write_csv(
        output_dir / "province_class_capacity.csv",
        v2.PROVINCE_CLASS_FIELDS,
        province_class_rows,
    )
    (output_dir / "NATIONAL_FACILITY_CAPACITY_V3.md").write_text(
        _report(summary), encoding="utf-8"
    )
    v2.v1._write_json(output_dir / "summary.json", summary)
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": v2.v1._sha256(path)}
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
        "counts": summary["counts"],
        "coverage": summary["coverage"],
        "match": summary["match"],
        "validation": summary["validation"],
    }
    v2.v1._write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "records": summary["counts"]["records_total"],
                "published_records": summary["counts"]["published_records"],
                "proxy_records": summary["counts"]["proxy_records"],
                "published_capacity_m3_y": summary["capacity"][
                    "published_capacity_m3_y"
                ],
                "total_capacity_m3_y": summary["capacity"][
                    "total_capacity_m3_y"
                ],
                "match": summary["match"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
