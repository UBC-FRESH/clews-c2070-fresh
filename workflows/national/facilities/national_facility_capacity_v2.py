"""Build national facility capacity v2 from inventory v1.

v2 carries every v1 published capacity, including the Canadian Biomass 2025
pellet-map values converted to finished-pellet m3 at a documented 650 kg/m3
bulk density.  Blank relevant-class records still receive the BC class-median
proxy.  Converted values are labelled ``published_capacity_converted`` and
explicitly distinguished from the 2015 Mills DB/FFCM infeed proxy.

All values are model inputs: ``observed=false`` and ``policy_grade=false``.
No legal or AAC claim is made.  No restricted data are used.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_capacity as v1,
)
from workflows.national.facilities import (
    pellet_map_2025,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v1"
    / "national_facilities.csv"
)
INVENTORY_V0_SUMMARY = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v0"
    / "summary.json"
)
CAPACITY_V1_SUMMARY = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v1"
    / "summary.json"
)
OUTPUT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_facility_capacity_v2"
)
ARTIFACT = "national_facility_capacity_v2"
ARTIFACT_VERSION = "2.0.0"

PUBLISHED = v1.PUBLISHED
PROXY = v1.PROXY
NOT_PUBLISHED = v1.NOT_PUBLISHED
PUBLISHED_INFEED_EVIDENCE = "published_mills_db_2015_infeed_proxy"
PUBLISHED_CONVERTED_EVIDENCE = "published_capacity_converted"
PROXY_EVIDENCE = "class_median_proxy"
PROXY_SOURCE_ID = v1.PROXY_SOURCE_ID
EVIDENCE_LABEL = v1.EVIDENCE_LABEL
OBSERVED = v1.OBSERVED
POLICY_GRADE = v1.POLICY_GRADE
DENSITY_T_PER_M3 = pellet_map_2025.DENSITY_T_PER_M3

CAPACITY_FIELDS = [
    *v1.CAPACITY_FIELDS,
    "capacity_basis",
    "conversion_density_t_per_m3",
]
CLASS_MEDIAN_FIELDS = v1.CLASS_MEDIAN_FIELDS
PROVINCE_CLASS_FIELDS = v1.PROVINCE_CLASS_FIELDS
SOURCE_FIELDS = v1.SOURCE_FIELDS


class CapacityV2Error(v1.CapacityError):
    """Raised when a capacity v2 invariant fails."""


def _read_evidence(path: Path = INVENTORY_CSV) -> dict[str, str]:
    rows = v1._read_csv(path)
    v1._require_columns(rows, {"facility_id", "capacity_evidence"}, path)
    evidence: dict[str, str] = {}
    for row in rows:
        facility_id = row["facility_id"].strip()
        if facility_id in evidence:
            raise CapacityV2Error(f"{path}: duplicate facility_id {facility_id}")
        evidence[facility_id] = row["capacity_evidence"].strip()
    return evidence


def _converted_note(row: dict[str, Any]) -> str:
    # The capacity itself is already converted; the exact tonnes value lives in
    # the inventory crosswalk and pellet-map extract.  Keep this note focused
    # on the conversion basis.
    return (
        "Published capacity converted from Canadian Biomass 2025 pellet-map "
        f"finished pellet tonnes/yr at {DENSITY_T_PER_M3} t/m3 to "
        f"{float(row['capacity_m3_y']):,.0f} m3/yr finished-pellet bulk "
        "volume. Not infeed-equivalent; no pellet yield applied."
    )


def build_capacity_rows(
    records: Sequence[v1.InventoryRecord],
    anchors: dict[str, v1.ClassAnchor],
    evidence: dict[str, str],
) -> list[dict[str, Any]]:
    rows = v1.build_capacity_rows(records, anchors)
    for row in rows:
        facility_id = row["facility_id"]
        status = row["capacity_status"]
        if status == PUBLISHED:
            label = evidence.get(facility_id, "")
            if not label:
                label = PUBLISHED_INFEED_EVIDENCE
            row["capacity_evidence"] = label
            if label == PUBLISHED_CONVERTED_EVIDENCE:
                row["capacity_basis"] = (
                    "published_finished_pellets_converted_650kg_m3"
                )
                row["conversion_density_t_per_m3"] = str(DENSITY_T_PER_M3)
                row["notes"] = _converted_note(row)
            else:
                row["capacity_basis"] = "published_infeed_proxy_2015"
                row["conversion_density_t_per_m3"] = ""
        elif status == PROXY:
            row["capacity_evidence"] = PROXY_EVIDENCE
            row["capacity_basis"] = "class_median_proxy_bc_anchor"
            row["conversion_density_t_per_m3"] = ""
        else:
            row["capacity_evidence"] = ""
            row["capacity_basis"] = "unavailable"
            row["conversion_density_t_per_m3"] = ""
    return rows


def _sources(retrieved_utc: str) -> list[dict[str, str]]:
    return [
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
            "access_method": (
                "Carried through national_facility_inventory_v1 from "
                "national_facility_inventory_v0"
            ),
            "retrieved_utc": retrieved_utc,
            "coverage": "British Columbia, 179 published infeed-proxy records",
            "access_status": "available",
            "used_for_capacity": "yes_published_anchor",
            "notes": (
                "Published capacities are 2015 Mills DB/FFCM processing-line "
                "infeed proxies, not licensed or current capacity."
            ),
        },
        {
            "source_id": PROXY_SOURCE_ID,
            "title": "Class-median capacity proxy derived from BC anchors",
            "publisher": "UBC FRESH",
            "source_url": (
                "data/derived/national/facilities/national_facility_capacity_v2/"
                "class_medians.csv"
            ),
            "licence": (
                "Model-derived proxy. Anchor values inherit the BC Mills DB / "
                "FFCM lineage; not a facility measurement and not relicensed."
            ),
            "licence_url": "",
            "access_method": "Computed from national_facility_inventory_v1",
            "retrieved_utc": retrieved_utc,
            "coverage": "All provinces and territories",
            "access_status": "computed",
            "used_for_capacity": "yes_proxy",
            "notes": (
                "Median capacity by facility class from BC published infeed "
                "records, applied to blank records of the same class. The "
                "p25-p75 range is reported; no provincial scaling is applied."
            ),
        },
        {
            "source_id": pellet_map_2025.SOURCE_ID,
            "title": pellet_map_2025.SOURCE_TITLE,
            "publisher": pellet_map_2025.PUBLISHER,
            "source_url": pellet_map_2025.SOURCE_URL,
            "licence": pellet_map_2025.LICENCE,
            "licence_url": "",
            "access_method": "Public HTTP PDF, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "43 current Canadian pellet producers; 4 proposed excluded",
            "access_status": "available",
            "used_for_capacity": "yes_published_capacity_converted",
            "notes": (
                "Current-producer capacities in finished pellet tonnes/yr, "
                "converted at 650 kg/m3 to finished-pellet bulk volume and "
                "labelled published_capacity_converted."
            ),
        },
        {
            "source_id": pellet_map_2025.DENSITY_SOURCE_ID,
            "title": pellet_map_2025.DENSITY_SOURCE_TITLE,
            "publisher": pellet_map_2025.DENSITY_SOURCE_PUBLISHER,
            "source_url": pellet_map_2025.DENSITY_SOURCE_URL,
            "licence": (
                "Government of Canada publication; factual bulk-density range "
                "cited, not reproduced."
            ),
            "licence_url": "",
            "access_method": "Public HTTP PDF, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Bulk density 600-750 kg/m3 for graded wood pellets",
            "access_status": "available",
            "used_for_capacity": "yes_conversion_basis",
            "notes": (
                "CAN/CSA-ISO 17225-2 bulk-density range 600-750 kg/m3. The "
                "planning conversion uses 650 kg/m3, inside that range."
            ),
        },
        {
            "source_id": "osm_nominatim_city_centroid",
            "title": "OpenStreetMap Nominatim city-centroid geocoding",
            "publisher": "OpenStreetMap contributors",
            "source_url": "https://nominatim.openstreetmap.org/",
            "licence": "ODbL-1.0",
            "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "access_method": "Public HTTP API, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Pellet-map plant cities",
            "access_status": "available",
            "used_for_capacity": "no_capacity_location_only",
            "notes": "City centroids locate new pellet records; they do not "
            "provide capacity.",
        },
        {
            "source_id": "alberta_forest_products_members",
            "title": "Alberta Forest Products Association association members",
            "publisher": "Alberta Forest Products Association",
            "source_url": "https://albertaforestproducts.ca/about/association-members/",
            "licence": "Public website; factual member listing, all rights reserved",
            "licence_url": "",
            "access_method": "Public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Alberta member companies and mill towns",
            "access_status": "available_no_capacity",
            "used_for_capacity": "no",
            "notes": (
                "Listed companies and towns but no facility-level annual "
                "capacity; used only as a name cross-check."
            ),
        },
        {
            "source_id": "ofia_members",
            "title": "OFIA member directory",
            "publisher": "Ontario Forest Industries Association",
            "source_url": "https://ofia.com/our-members/",
            "licence": "Public website; all rights reserved",
            "licence_url": "",
            "access_method": "Public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Ontario member mills",
            "access_status": "available_no_capacity",
            "used_for_capacity": "no",
            "notes": "No facility-level annual capacity was exposed in the "
            "server-rendered member page.",
        },
    ]


def _read_coverage_baseline(path: Path = CAPACITY_V1_SUMMARY) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    capacity = payload.get("capacity", {})
    counts = payload.get("counts", {})
    return {
        "records_total": counts.get("records_total"),
        "published_records": counts.get("published_records"),
        "proxy_records": counts.get("proxy_records"),
        "not_published_records": counts.get("not_published_records"),
        "published_capacity_m3_y": capacity.get("published_capacity_m3_y"),
        "proxy_capacity_m3_y": capacity.get("proxy_capacity_m3_y"),
        "total_capacity_m3_y": capacity.get("total_capacity_m3_y"),
    }


def _coverage(summary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    counts = summary["counts"]
    capacity = summary["capacity"]
    total = float(capacity["total_capacity_m3_y"])
    after = {
        "records_total": counts["records_total"],
        "published_records": counts["published_records"],
        "proxy_records": counts["proxy_records"],
        "not_published_records": counts["not_published_records"],
        "published_capacity_m3_y": capacity["published_capacity_m3_y"],
        "proxy_capacity_m3_y": capacity["proxy_capacity_m3_y"],
        "total_capacity_m3_y": capacity["total_capacity_m3_y"],
        "published_share_records": (
            counts["published_records"] / counts["records_total"]
            if counts["records_total"]
            else None
        ),
        "proxy_share_records": (
            counts["proxy_records"] / counts["records_total"]
            if counts["records_total"]
            else None
        ),
        "published_share_capacity": (
            capacity["published_capacity_m3_y"] / total if total else None
        ),
        "proxy_share_capacity": (
            capacity["proxy_capacity_m3_y"] / total if total else None
        ),
    }
    before = dict(baseline)
    before_total = before.get("total_capacity_m3_y")
    if before_total:
        before["published_share_records"] = (
            before.get("published_records", 0) / before["records_total"]
            if before.get("records_total")
            else None
        )
        before["proxy_share_records"] = (
            before.get("proxy_records", 0) / before["records_total"]
            if before.get("records_total")
            else None
        )
        before["published_share_capacity"] = (
            before.get("published_capacity_m3_y", 0.0) / before_total
        )
        before["proxy_share_capacity"] = (
            before.get("proxy_capacity_m3_y", 0.0) / before_total
        )
    return {"before": before, "after": after}


def _summary(
    *,
    generated_utc: str,
    records: Sequence[v1.InventoryRecord],
    anchors: dict[str, v1.ClassAnchor],
    capacity_rows: Sequence[dict[str, Any]],
    class_medians: Sequence[dict[str, Any]],
    province_class: Sequence[dict[str, Any]],
    sources: Sequence[dict[str, str]],
) -> dict[str, Any]:
    summary = v1._summary(
        generated_utc=generated_utc,
        records=records,
        capacity_rows=capacity_rows,
        anchors=anchors,
        class_medians=class_medians,
        province_class=province_class,
        sources=sources,
    )
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["method"] = {
        "inventory": "national_facility_inventory_v1",
        "published_rule": (
            "carry every published inventory capacity unchanged; converted "
            "pellet-map values remain on a finished-pellet bulk-volume basis"
        ),
        "proxy_rule": (
            "for a blank relevant-class record, assign the median capacity of "
            "BC published infeed-proxy records in the same facility class"
        ),
        "anchor_province": v1.ANCHOR_PROVINCE,
        "anchor_classes": list(v1.RELEVANT_CLASSES),
        "anchor_min_records": v1.MIN_ANCHOR_RECORDS,
        "anchor_basis": (
            "BC published infeed-proxy records; converted pellet volumes are "
            "not used as proxy anchors because their unit basis differs"
        ),
        "proxy_range": "p25-p75 of the BC class anchor",
        "quantile_method": v1.ANCHOR_QUANTILE_METHOD,
        "province_scaling": "none: province_scale=1 for every proxy",
        "unavailable_rule": (
            "a class without a BC anchor is left blank and excluded from "
            "assignment rather than given a fabricated capacity"
        ),
        "rounding": "anchor median and p25/p75 are rounded to whole m3/yr",
        "capacity_units": (
            "m3/yr mixed evidence: 2015 infeed proxy, converted finished-"
            "pellet bulk volume, and BC class-median proxy"
        ),
        "pellet_conversion": {
            "density_t_per_m3": DENSITY_T_PER_M3,
            "density_range_t_per_m3": [
                pellet_map_2025.DENSITY_MIN_T_PER_M3,
                pellet_map_2025.DENSITY_MAX_T_PER_M3,
            ],
            "density_source_id": pellet_map_2025.DENSITY_SOURCE_ID,
            "formula": "capacity_m3_y = capacity_tonnes_y / density",
            "basis": (
                "finished-pellet bulk volume; no pellet yield or infeed "
                "conversion applied"
            ),
        },
    }
    summary["inputs"] = {
        str(INVENTORY_CSV.relative_to(REPO_ROOT)): v1._sha256(INVENTORY_CSV)
    }
    capacity = summary["capacity"]
    total = float(capacity["total_capacity_m3_y"])
    capacity["published_share_of_total"] = (
        capacity["published_capacity_m3_y"] / total if total else None
    )
    capacity["published_share_of_records"] = (
        summary["counts"]["published_records"]
        / summary["counts"]["records_total"]
    )
    capacity["proxy_share_of_records"] = (
        summary["counts"]["proxy_records"] / summary["counts"]["records_total"]
    )
    for province, block in capacity["by_province"].items():
        group = [row for row in capacity_rows if row["province"] == province]
        block["published_capacity_m3_y"] = sum(
            float(row["capacity_m3_y"])
            for row in group
            if row["capacity_status"] == PUBLISHED
        )
        block["proxy_capacity_m3_y"] = sum(
            float(row["capacity_m3_y"])
            for row in group
            if row["capacity_status"] == PROXY
        )
    converted = [
        row
        for row in capacity_rows
        if row.get("capacity_evidence") == PUBLISHED_CONVERTED_EVIDENCE
    ]
    infeed = [
        row
        for row in capacity_rows
        if row.get("capacity_evidence") == PUBLISHED_INFEED_EVIDENCE
    ]
    summary["capacity"]["evidence"] = {
        "published_infeed_proxy_records": len(infeed),
        "published_infeed_proxy_capacity_m3_y": sum(
            float(row["capacity_m3_y"]) for row in infeed
        ),
        "published_converted_records": len(converted),
        "published_converted_capacity_m3_y": sum(
            float(row["capacity_m3_y"]) for row in converted
        ),
        "proxy_records": summary["counts"]["proxy_records"],
        "proxy_capacity_m3_y": capacity["proxy_capacity_m3_y"],
        "not_published_records": summary["counts"]["not_published_records"],
    }
    summary["coverage"] = _coverage(summary, _read_coverage_baseline())
    summary["sources"] = list(sources)
    summary["validation"].update(
        {
            "all_published_rows_have_evidence": all(
                row.get("capacity_evidence")
                in {PUBLISHED_INFEED_EVIDENCE, PUBLISHED_CONVERTED_EVIDENCE}
                for row in capacity_rows
                if row["capacity_status"] == PUBLISHED
            ),
            "all_proxy_rows_have_class_median_evidence": all(
                row.get("capacity_evidence") == PROXY_EVIDENCE
                for row in capacity_rows
                if row["capacity_status"] == PROXY
            ),
            "converted_rows_use_documented_density": all(
                abs(float(row["conversion_density_t_per_m3"]) - DENSITY_T_PER_M3)
                <= 1e-12
                for row in converted
            ),
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
        }
    )
    summary["caveats"] = [
        ("All volumes are model estimates (`model_estimate`); `observed=false` "
        "and `policy_grade=false`."),
        ("Published capacity is mixed-basis: 2015 Mills DB/FFCM processing-line "
        "infeed proxies for BC and converted finished-pellet bulk volume for "
        "the 2025 pellet map. The two bases are not interchangeable."),
        ("Converted pellet-map capacities are finished-pellet volume, not "
        "infeed-equivalent. No pellet yield, moisture, bark, grade, or recovery "
        "conversion is applied."),
        ("Class-median proxies are statistical placeholders, not facility "
        "measurements. Their p25-p75 range is reported and they remain "
        "labelled `class_median_proxy`."),
        ("The anchor distribution is BC-only and uses infeed-proxy records; "
        "converted pellet volumes are excluded from the anchor because their "
        "basis differs. Applying BC medians to other provinces assumes "
        "comparable class structure."),
        ("No provincial scaling is applied in v2. A province's harvest share "
        "does not change its facility proxy capacity."),
        ("OTHER-class records have no BC anchor and no reported pellet capacity; "
        "they remain blank and are excluded from assignment."),
        ("These capacities are planning inputs only. They are not a legal/AAC "
        "determination, a timber-supply approval, or an observed operating "
        "capacity."),
        ("No restricted or proprietary data are used. No UI or public OSeCAN "
        "surface is changed by this artifact."),
    ]
    return summary


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def _report(summary: dict[str, Any]) -> str:
    coverage = summary["coverage"]
    before = coverage["before"]
    after = coverage["after"]
    lines = [
        "# National Facility Capacity V2",
        "",
        ("Capacity v1 plus the public Canadian Biomass 2025 pellet-map values "
        "converted to finished-pellet m3 at 650 kg/m3. All values are model "
        "estimates: `observed=false`, `policy_grade=false`."),
        "",
        "## Coverage before and after",
        "",
        *_md_table(
            ["Measure", "Capacity v1", "Capacity v2", "Delta"],
            [
                [
                    "Records",
                    before.get("records_total", ""),
                    after["records_total"],
                    after["records_total"] - before.get("records_total", 0),
                ],
                [
                    "Published records",
                    before.get("published_records", ""),
                    after["published_records"],
                    after["published_records"]
                    - before.get("published_records", 0),
                ],
                [
                    "Proxy records",
                    before.get("proxy_records", ""),
                    after["proxy_records"],
                    after["proxy_records"] - before.get("proxy_records", 0),
                ],
                [
                    "Not published records",
                    before.get("not_published_records", ""),
                    after["not_published_records"],
                    after["not_published_records"]
                    - before.get("not_published_records", 0),
                ],
                [
                    "Published capacity m3/yr",
                    f"{before.get('published_capacity_m3_y', 0):,.0f}",
                    f"{after['published_capacity_m3_y']:,.0f}",
                    f"{after['published_capacity_m3_y'] - before.get('published_capacity_m3_y', 0):,.0f}",
                ],
                [
                    "Proxy capacity m3/yr",
                    f"{before.get('proxy_capacity_m3_y', 0):,.0f}",
                    f"{after['proxy_capacity_m3_y']:,.0f}",
                    f"{after['proxy_capacity_m3_y'] - before.get('proxy_capacity_m3_y', 0):,.0f}",
                ],
                [
                    "Published share of records",
                    f"{before.get('published_share_records', 0):.1%}",
                    f"{after['published_share_records']:.1%}",
                    f"{after['published_share_records'] - before.get('published_share_records', 0):.1%}",
                ],
                [
                    "Proxy share of records",
                    f"{before.get('proxy_share_records', 0):.1%}",
                    f"{after['proxy_share_records']:.1%}",
                    f"{after['proxy_share_records'] - before.get('proxy_share_records', 0):.1%}",
                ],
                [
                    "Published share of capacity",
                    f"{before.get('published_share_capacity', 0):.1%}",
                    f"{after['published_share_capacity']:.1%}",
                    f"{after['published_share_capacity'] - before.get('published_share_capacity', 0):.1%}",
                ],
                [
                    "Proxy share of capacity",
                    f"{before.get('proxy_share_capacity', 0):.1%}",
                    f"{after['proxy_share_capacity']:.1%}",
                    f"{after['proxy_share_capacity'] - before.get('proxy_share_capacity', 0):.1%}",
                ],
            ],
        ),
        "",
        "## Evidence mix",
        "",
        *_md_table(
            ["Evidence", "Records", "Capacity m3/yr"],
            [
                [
                    "published infeed proxy (2015)",
                    summary["capacity"]["evidence"][
                        "published_infeed_proxy_records"
                    ],
                    f"{summary['capacity']['evidence']['published_infeed_proxy_capacity_m3_y']:,.0f}",
                ],
                [
                    "published converted (2025 pellet map)",
                    summary["capacity"]["evidence"][
                        "published_converted_records"
                    ],
                    f"{summary['capacity']['evidence']['published_converted_capacity_m3_y']:,.0f}",
                ],
                [
                    "class-median proxy",
                    summary["capacity"]["evidence"]["proxy_records"],
                    f"{summary['capacity']['evidence']['proxy_capacity_m3_y']:,.0f}",
                ],
                [
                    "not published / unavailable",
                    summary["capacity"]["evidence"]["not_published_records"],
                    "0",
                ],
            ],
        ),
        "",
        "## Conversion basis",
        "",
        "`capacity_m3_y = capacity_tonnes_y / 0.65`",
        "",
        ("The 650 kg/m3 value is inside the Natural Resources Canada / "
        "CAN-CSA-ISO 17225-2 range of 600-750 kg/m3. It is finished-pellet bulk "
        "volume, not infeed-equivalent. Converted rows carry "
        "`capacity_evidence=published_capacity_converted` and "
        "`capacity_basis=published_finished_pellets_converted_650kg_m3`."),
        "",
        "## By class",
        "",
        *_md_table(
            ["Class", "Records", "Published", "Proxy", "Unavailable"],
            [
                [
                    facility_class,
                    block["records"],
                    block["published"],
                    block["proxy"],
                    block["unavailable"],
                ]
                for facility_class, block in summary["capacity"][
                    "by_class"
                ].items()
            ],
        ),
        "",
        "## By province",
        "",
        *_md_table(
            ["Province", "Records", "Published", "Proxy", "Unavailable"],
            [
                [
                    province,
                    block["records"],
                    block["published"],
                    block["proxy"],
                    block["unavailable"],
                ]
                for province, block in summary["capacity"][
                    "by_province"
                ].items()
                if block["records"]
            ],
        ),
        "",
        "## Validation",
        "",
    ]
    for key, value in summary["validation"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Caveats", ""])
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


def _write_manifest(output_dir: Path, summary: dict[str, Any]) -> None:
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": v1._sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "counts": summary["counts"],
        "capacity": summary["capacity"],
        "coverage": summary["coverage"],
        "anchors": summary["anchors"],
        "sources": summary["sources"],
        "validation": summary["validation"],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_capacity_v2"
        ),
    }
    v1._write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    inventory_path: Path = INVENTORY_CSV,
) -> dict[str, Any]:
    """Build capacity v2 and return its summary."""

    generated_utc = v1._utc_now()
    records = v1.read_inventory(inventory_path)
    evidence = _read_evidence(inventory_path)
    # Converted pellet volumes use a different unit basis from the 2015
    # infeed-proxy anchors, so they are excluded from the anchor distribution.
    anchor_records = tuple(
        record
        for record in records
        if evidence.get(record.facility_id, PUBLISHED_INFEED_EVIDENCE)
        == PUBLISHED_INFEED_EVIDENCE
    )
    anchors = v1.compute_anchors(anchor_records)
    capacity_rows = build_capacity_rows(records, anchors, evidence)
    class_medians = v1.build_class_medians(anchors, capacity_rows)
    for row in class_medians:
        row["notes"] = (
            "Median and p25/p75 are computed only from BC published "
            "infeed-proxy records; converted pellet volumes are excluded "
            "because their unit basis differs."
        )
    province_class = v1.build_province_class(capacity_rows)
    sources = _sources(generated_utc)
    summary = _summary(
        generated_utc=generated_utc,
        records=records,
        anchors=anchors,
        capacity_rows=capacity_rows,
        class_medians=class_medians,
        province_class=province_class,
        sources=sources,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    v1._write_csv(
        output_dir / "national_facility_capacity_v2.csv",
        CAPACITY_FIELDS,
        capacity_rows,
    )
    v1._write_csv(
        output_dir / "class_medians.csv", CLASS_MEDIAN_FIELDS, class_medians
    )
    v1._write_csv(
        output_dir / "province_class_capacity.csv",
        PROVINCE_CLASS_FIELDS,
        province_class,
    )
    v1._write_csv(output_dir / "sources.csv", SOURCE_FIELDS, sources)
    (output_dir / "NATIONAL_FACILITY_CAPACITY_V2.md").write_text(
        _report(summary), encoding="utf-8"
    )
    v1._write_json(output_dir / "summary.json", summary)
    _write_manifest(output_dir, summary)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "records_total": summary["counts"]["records_total"],
                "published_records": summary["counts"]["published_records"],
                "proxy_records": summary["counts"]["proxy_records"],
                "published_capacity_m3_y": summary["capacity"][
                    "published_capacity_m3_y"
                ],
                "proxy_capacity_m3_y": summary["capacity"][
                    "proxy_capacity_m3_y"
                ],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
