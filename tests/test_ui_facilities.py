"""Contract tests for the read-only Facilities view and its static export."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.ui import export_facilities_ui as exporter

BUNDLE = ROOT / "ui/public/data/facilities"
CAPACITY = ROOT / "data/derived/national/facilities/national_facility_capacity_v3"
ASSIGNMENT = ROOT / "data/derived/national/facilities/national_facility_assignment_v6"
CHAIN = ROOT / "data/derived/national/facilities/national_facility_chain_scenarios_v6"
APP_JS = ROOT / "ui/app.js"
INDEX_HTML = ROOT / "ui/index.html"
STYLES_CSS = ROOT / "ui/styles.css"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads((BUNDLE / "facilities.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))


def test_export_has_689_typed_records(payload: dict) -> None:
    assert payload["schema_version"] == "facilities/1"
    assert payload["labels"] == {
        "evidence": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "ranking": "none",
        "official_osecan": False,
        "read_only": True,
    }
    assert payload["coverage"]["record_count"] == len(payload["facilities"]) == 689
    assert len({row["id"] for row in payload["facilities"]}) == 689
    assert payload["coverage"]["class_count"] == 6
    assert payload["coverage"]["province_count"] == 11
    assert sum(item["record_count"] for item in payload["classes"]) == 689
    assert sum(item["record_count"] for item in payload["provinces"]) == 689
    assert [item["id"] for item in payload["classes"]] == list(exporter.CLASS_ORDER)
    for row in payload["facilities"]:
        assert row["facility_class"] in exporter.CLASS_ORDER
        assert row["province"] in exporter.PROVINCE_LABELS
        assert row["capacity_status"] in {"published", "proxy", "not_published"}
        assert row["mapped"] is (row["lat"] is not None and row["lon"] is not None)
        if row["mapped"]:
            assert -141.0 <= row["lon"] <= -52.0
            assert 41.0 <= row["lat"] <= 84.0
        else:
            assert row["lat"] is None and row["lon"] is None


def test_map_counts_and_unlocated_records(payload: dict) -> None:
    coverage = payload["coverage"]
    mapped = [row for row in payload["facilities"] if row["mapped"]]
    unlocated = [row for row in payload["facilities"] if not row["mapped"]]
    assert len(mapped) == coverage["mapped_count"] == 608
    assert len(unlocated) == coverage["unlocated_count"] == 81
    assert len(mapped) + len(unlocated) == 689
    assert {row["province"] for row in unlocated} == {"BC"}
    assert coverage["unlocated_provinces"] == ["BC"]
    assert all(row["mapped"] for row in mapped)
    assert "no location is invented" in payload["caveats"]["location"]
    capacity_rows = _read_csv(CAPACITY / "national_facility_capacity_v3.csv")
    assert sum(1 for row in capacity_rows if row["lat"] and row["lon"]) == 608
    assert sum(1 for row in capacity_rows if not row["lat"] and not row["lon"]) == 81


def test_table_counts_and_capacity_match_capacity_v3(payload: dict) -> None:
    counts = json.loads((CAPACITY / "manifest.json").read_text(encoding="utf-8"))["counts"]
    assert payload["coverage"]["published_count"] == counts["published_records"] == 433
    assert payload["coverage"]["proxy_count"] == counts["proxy_records"] == 241
    assert payload["coverage"]["not_published_count"] == counts["not_published_records"] == 15
    assert payload["coverage"]["published_capacity_m3_y"] == pytest.approx(166_608_791.456421, abs=1e-6)
    assert payload["coverage"]["proxy_capacity_m3_y"] == pytest.approx(104_733_008.0, abs=1e-6)
    for province in payload["provinces"]:
        source = [
            row
            for row in _read_csv(CAPACITY / "national_facility_capacity_v3.csv")
            if row["province"] == province["id"]
        ]
        assert province["record_count"] == len(source)
        assert province["published_count"] == sum(1 for row in source if row["capacity_status"] == "published")
        assert province["proxy_count"] == sum(1 for row in source if row["capacity_status"] == "proxy")
    for scenario in payload["scenarios"]:
        assert len(scenario["rows"]) == 38
        assert sum(row["record_count"] for row in scenario["rows"]) == 689
        assert sum(row["published_count"] for row in scenario["rows"]) == 433
        assert sum(row["proxy_count"] for row in scenario["rows"]) == 241
        assert sum(row["not_published_count"] for row in scenario["rows"]) == 15
        assert sum(row["published_capacity_m3_y"] for row in scenario["rows"]) == pytest.approx(
            payload["coverage"]["published_capacity_m3_y"], abs=1e-6
        )
        assert sum(row["proxy_capacity_m3_y"] for row in scenario["rows"]) == pytest.approx(
            payload["coverage"]["proxy_capacity_m3_y"], abs=1e-6
        )


def test_scenarios_reproduce_chain_and_assignment_v6(payload: dict) -> None:
    chain_rows = _read_csv(CHAIN / "scenario_assignment.csv")
    chain_summary = {row["scenario"]: row for row in _read_csv(CHAIN / "scenario_summary.csv")}
    assert [scenario["id"] for scenario in payload["scenarios"]] == ["100m", "119m", "150m"]
    assert payload["default_scenario"] == "119m"
    for scenario in payload["scenarios"]:
        rows = [row for row in chain_rows if row["scenario"] == scenario["id"]]
        summary = chain_summary[scenario["id"]]
        assigned = sum(float(row["assigned_m3"]) for row in rows)
        assert scenario["assigned_m3"] == pytest.approx(assigned, abs=1e-6)
        assert scenario["harvest_m3"] == pytest.approx(float(summary["harvest_m3"]), abs=1e-6)
        assert scenario["facilities_used"] == int(summary["facilities_used"])
        assert scenario["capacity_utilization"] == pytest.approx(
            float(summary["capacity_utilization"]), abs=1e-9
        )
        assert sum(row["assigned_m3_y"] for row in scenario["rows"]) == pytest.approx(assigned, abs=1e-6)
        eligible = sum(row["eligible_capacity_m3_y"] for row in scenario["rows"])
        assert assigned / eligible == pytest.approx(float(summary["capacity_utilization"]), abs=1e-6)
        assert len(scenario["facility_assignments"]) == len(rows)

    base = [
        row
        for row in _read_csv(ASSIGNMENT / "facility_assignment.csv")
        if row["scenario"] == "province_capacity_floor"
    ]
    scenario_119 = next(item for item in payload["scenarios"] if item["id"] == "119m")
    assert len(base) == 208 == scenario_119["facilities_used"]
    assert sum(float(row["assigned_m3"]) for row in base) == pytest.approx(
        scenario_119["assigned_m3"], abs=1e-6
    )
    assert set(scenario_119["facility_assignments"]) == {row["facility_id"] for row in base}


def test_province_and_class_filters_conserve_records(payload: dict) -> None:
    scenario = next(item for item in payload["scenarios"] if item["id"] == "119m")
    capacity_rows = _read_csv(CAPACITY / "national_facility_capacity_v3.csv")
    for province in ["BC", "QC", "AB", "ON", "YT"]:
        expected = sum(1 for row in capacity_rows if row["province"] == province)
        filtered = [row for row in scenario["rows"] if row["province"] == province]
        assert sum(row["record_count"] for row in filtered) == expected
        for row in filtered:
            assert row["province"] == province
    for class_id in exporter.CLASS_ORDER:
        expected = sum(1 for row in capacity_rows if row["facility_class"] == class_id)
        filtered = [row for row in scenario["rows"] if row["facility_class"] == class_id]
        assert sum(row["record_count"] for row in filtered) == expected
        for row in filtered:
            assert row["facility_class"] == class_id
    bc_saw = [
        row
        for row in scenario["rows"]
        if row["province"] == "BC" and row["facility_class"] == "SAW"
    ]
    assert sum(row["record_count"] for row in bc_saw) == 194
    assert bc_saw[0]["assigned_m3_y"] > 0
    assert bc_saw[0]["published_count"] == 121
    assert bc_saw[0]["proxy_count"] == 73
    assert bc_saw[0]["unlocated_count"] == 61


def test_manifest_hashes_cover_payload_and_sources(payload: dict, manifest: dict) -> None:
    assert manifest["schema_version"] == "facilities/1"
    assert manifest["validation"]["ok"] is True
    generated = manifest["generated"]
    assert len(generated) == 1
    assert generated[0]["path"] == "facilities/facilities.json"
    data = (BUNDLE / "facilities.json").read_bytes()
    assert generated[0]["bytes"] == len(data)
    assert generated[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert len(manifest["sources"]) == len(payload["source_files"]) == 8
    for source in manifest["sources"]:
        path = ROOT / source["path"]
        assert path.is_file()
        assert source["bytes"] == path.stat().st_size
        assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest["labels"] == payload["labels"]


def test_export_is_not_stale_against_source_csvs(payload: dict) -> None:
    fresh = exporter.build_payload(ROOT)
    committed = {key: value for key, value in payload.items() if key != "validation"}
    regenerated = {key: value for key, value in fresh.items() if key != "validation"}
    committed.pop("generated_utc", None)
    regenerated.pop("generated_utc", None)
    assert committed == regenerated


def test_facilities_ui_is_single_purpose_and_read_only(payload: dict) -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="tab-facilities"' in html
    assert 'data-tab="facilities"' in html
    assert 'id="panel-facilities"' in html
    assert 'id="facility-map"' in html
    assert 'id="facilities-table"' in html
    panel = html[html.index('id="panel-facilities"') : html.index('id="panel-zones"')]
    for numeric in ("689", "608", "433", "241", "166.61", "104.73", "118.977"):
        assert numeric not in panel, f"facility panel hard-codes {numeric}"
    ids = re.findall(r'id="([^"]+)"', panel)
    assert not [item for item in ids if item.startswith(("run-", "osecan-", "lp-"))]
    assert 'id="facility-scenario"' in panel
    assert 'id="facility-province"' in panel
    assert 'id="facility-class"' in panel
    assert 'id="facility-reset"' in panel

    app = APP_JS.read_text(encoding="utf-8")
    assert "facilities: {" in app
    assert "FACILITY_DATA_PATH = 'facilities/facilities.json'" in app
    assert "FACILITY_MANIFEST_PATH = 'facilities/manifest.json'" in app
    assert "'facilities'" in app
    assert "if (state.tab === 'facilities') initFacilities();" in app
    assert "function applyFacilityMapFilter()" in app
    assert "facility-province" in app and "facility-class" in app
    facility_code = app[app.index("/* ---------------- Facilities ---------------- */") : app.index("/* ---------------- Boot ---------------- */")]
    assert "fetch(" not in facility_code
    assert "apiFetch" not in facility_code
    assert "client." not in facility_code
    assert ".solve" not in facility_code
    assert "runId" not in facility_code
    assert payload["caveats"]["no_ranking"].startswith("This ledger")


def test_mobile_facility_table_stacks_without_horizontal_scroll() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    for label in ("Province", "Class", "Records", "Published capacity", "Proxy capacity", "Assigned", "Utilization", "Evidence"):
        assert f'data-label="{label}' in app
    assert "Swipe the table sideways" not in app
    css = STYLES_CSS.read_text(encoding="utf-8")
    marker = "/* Mobile stacked facility ledger: no horizontal table scroll. */"
    assert marker in css
    block = css[css.index(marker) : css.index("@media (max-width: 440px)", css.index(marker))]
    assert ".table-wrap.facilities-table-wrap" in block
    assert "overflow: visible" in block
    assert ".facilities-table thead { display: none; }" in block
    assert "grid-template-areas:" in block
    assert '"province class"' in block
    assert '"records assigned"' in block
    assert '"utilization utilization"' in block
    assert '"published proxy"' in block
    assert '"evidence evidence"' in block
    assert "min-width: 0" in block
    assert "min-width: 780px" not in block
