"""Regression tests for the transport-radius capacity gap v1 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from workflows.national.facilities import transport_capacity_gap_v1 as gap

OUTPUT_DIR = gap.OUTPUT_DIR
BANDS_CSV = OUTPUT_DIR / "province_class_distance_bands.csv"
GAPS_CSV = OUTPUT_DIR / "province_class_capacity_gap.csv"
PROVINCES_CSV = OUTPUT_DIR / "province_summary.csv"
PRIORITY_CSV = OUTPUT_DIR / "greenfield_local_capacity_priority.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "TRANSPORT_CAPACITY_GAP_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def summary() -> dict[str, Any]:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def bands() -> list[dict[str, str]]:
    return _read_csv(BANDS_CSV)


@pytest.fixture(scope="module")
def gaps() -> list[dict[str, str]]:
    return _read_csv(GAPS_CSV)


@pytest.fixture(scope="module")
def provinces() -> list[dict[str, str]]:
    return _read_csv(PROVINCES_CSV)


@pytest.fixture(scope="module")
def priority() -> list[dict[str, str]]:
    return _read_csv(PRIORITY_CSV)


def test_outputs_exist_manifest_hashes_and_labels(summary) -> None:
    for path in (
        BANDS_CSV,
        GAPS_CSV,
        PROVINCES_CSV,
        PRIORITY_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(BANDS_CSV)[0]) == gap.BAND_FIELDS
    assert list(_read_csv(GAPS_CSV)[0]) == gap.GAP_FIELDS
    assert list(_read_csv(PROVINCES_CSV)[0]) == gap.PROVINCE_FIELDS
    assert list(_read_csv(PRIORITY_CSV)[0]) == gap.PRIORITY_FIELDS

    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "transport_capacity_gap_v1"
    assert manifest["artifact_version"] == "1.0.0"
    assert manifest["status"] == "complete"
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert _sha256(path) == entry["sha256"], name

    labels = summary["labels"]
    assert labels["evidence_label"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["national_claim"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["ui_changed"] is False
    assert labels["siting_optimization"] is False
    assert labels["policy_claim"] is False

    report = REPORT_MD.read_text(encoding="utf-8")
    assert "Centroid-origin caveat" in report
    assert "observed=false" in report
    assert "policy_grade=false" in report
    assert "not a national" in report.lower()
    assert "no siting optimization" in report.lower()


def test_band_sums_conserve_assigned_volume(bands, gaps) -> None:
    offer_total = math.fsum(
        offer.volume_m3 for offer in gap.read_offer_rows()
    )
    for scenario in gap.SCENARIOS:
        scenario_rows = [row for row in bands if row["scenario"] == scenario]
        assert math.fsum(
            float(row["assigned_m3"]) for row in scenario_rows
        ) == pytest.approx(offer_total, abs=1e-3)

    band_by_cell: dict[tuple[str, str, str], float] = {}
    for row in bands:
        key = (row["scenario"], row["province"], row["facility_class"])
        band_by_cell[key] = band_by_cell.get(key, 0.0) + float(
            row["assigned_m3"]
        )
    for row in gaps:
        key = (row["scenario"], row["province"], row["facility_class"])
        assert band_by_cell[key] == pytest.approx(
            float(row["assigned_m3"]), abs=1e-3
        )

    for key, assigned in band_by_cell.items():
        if assigned <= 0.0:
            continue
        shares = math.fsum(
            float(row["assigned_share_of_province_class"])
            for row in bands
            if (
                row["scenario"],
                row["province"],
                row["facility_class"],
            )
            == key
        )
        assert shares == pytest.approx(1.0, abs=1e-6)


def test_band_capacity_comparison_and_headroom_identity(bands, gaps) -> None:
    for row in bands:
        assigned = float(row["assigned_m3"])
        local = float(row["eligible_local_capacity_m3_y"])
        cross = float(row["cross_border_assigned_m3"])
        assert assigned <= local + cross + 1e-3
        assert float(row["local_capacity_deficit_m3_y"]) == pytest.approx(
            max(0.0, assigned - local), abs=1e-3
        )
        assert float(row["unused_local_capacity_m3_y"]) == pytest.approx(
            max(0.0, local - assigned), abs=1e-3
        )
        assert float(row["local_capacity_headroom_m3_y"]) == pytest.approx(
            local - assigned, abs=1e-3
        )

    capacity_by_cell: dict[tuple[str, str, str], float] = {}
    for row in bands:
        key = (row["scenario"], row["province"], row["facility_class"])
        capacity_by_cell[key] = capacity_by_cell.get(key, 0.0) + float(
            row["eligible_local_capacity_m3_y"]
        )
    for row in gaps:
        key = (row["scenario"], row["province"], row["facility_class"])
        assert capacity_by_cell[key] == pytest.approx(
            float(row["eligible_local_capacity_m3_y"]), abs=1e-3
        )
        capacity_50 = float(
            row["eligible_local_capacity_within_50_m3_y"]
        )
        capacity_150 = float(
            row["eligible_local_capacity_within_150_m3_y"]
        )
        capacity_300 = float(
            row["eligible_local_capacity_within_300_m3_y"]
        )
        assert capacity_50 <= capacity_150 + 1e-3
        assert capacity_150 <= capacity_300 + 1e-3
        assert capacity_300 <= float(
            row["eligible_local_capacity_m3_y"]
        ) + 1e-3
        assert float(row["local_access_gap_within_150_m3_y"]) == pytest.approx(
            max(0.0, float(row["assigned_m3"]) - capacity_150), abs=1e-3
        )
        assert float(row["local_access_gap_within_300_m3_y"]) == pytest.approx(
            max(0.0, float(row["assigned_m3"]) - capacity_300), abs=1e-3
        )


def test_stranded_and_underserved_partitions(gaps, provinces) -> None:
    for scenario in gap.SCENARIOS:
        scenario_rows = [row for row in gaps if row["scenario"] == scenario]
        assigned = math.fsum(float(row["assigned_m3"]) for row in scenario_rows)
        within_150 = math.fsum(
            float(row["assigned_within_150_m3_y"]) for row in scenario_rows
        )
        gt150 = math.fsum(
            float(row["assigned_gt150_m3_y"]) for row in scenario_rows
        )
        within_300 = math.fsum(
            float(row["assigned_within_300_m3_y"]) for row in scenario_rows
        )
        gt300 = math.fsum(
            float(row["assigned_gt300_m3_y"]) for row in scenario_rows
        )
        assert within_150 + gt150 == pytest.approx(assigned, abs=1e-3)
        assert within_300 + gt300 == pytest.approx(assigned, abs=1e-3)
        assert gt300 <= gt150 + 1e-3

        province_rows = [
            row for row in provinces if row["scenario"] == scenario
        ]
        assert math.fsum(
            float(row["assigned_m3"]) for row in province_rows
        ) == pytest.approx(assigned, abs=1e-3)
        assert math.fsum(
            float(row["assigned_gt300_m3_y"]) for row in province_rows
        ) == pytest.approx(gt300, abs=1e-3)
        for row in province_rows:
            row_assigned = float(row["assigned_m3"])
            row_gt150 = float(row["assigned_gt150_m3_y"])
            row_gt300 = float(row["assigned_gt300_m3_y"])
            assert float(row["assigned_gt150_share"]) == pytest.approx(
                row_gt150 / row_assigned, abs=1e-6
            )
            assert float(row["assigned_gt300_share"]) == pytest.approx(
                row_gt300 / row_assigned, abs=1e-6
            )
            expected_gt150_flag = (
                row_assigned >= gap.HIGH_EXPOSURE_MIN_M3
                and row_gt150 / row_assigned >= gap.HIGH_EXPOSURE_SHARE
            )
            expected_gt300_flag = (
                row_assigned >= gap.HIGH_EXPOSURE_MIN_M3
                and row_gt300 / row_assigned >= gap.HIGH_EXPOSURE_SHARE
            )
            assert row["high_gt150_exposure"] == (
                "true" if expected_gt150_flag else "false"
            )
            assert row["high_gt300_exposure"] == (
                "true" if expected_gt300_flag else "false"
            )


def test_no_nan_or_negative() -> None:
    numeric_fields = {
        "assigned_m3",
        "assigned_within_50_m3_y",
        "assigned_within_150_m3_y",
        "assigned_within_300_m3_y",
        "assigned_gt150_m3_y",
        "assigned_gt300_m3_y",
        "eligible_local_capacity_m3_y",
        "eligible_local_capacity_within_50_m3_y",
        "eligible_local_capacity_within_150_m3_y",
        "eligible_local_capacity_within_300_m3_y",
        "local_access_gap_within_150_m3_y",
        "local_access_gap_within_300_m3_y",
        "local_capacity_deficit_m3_y",
        "local_capacity_deficit_total_m3_y",
        "unused_local_capacity_m3_y",
        "transport_cost_cad",
        "transport_cost_cad_m3",
        "transport_cost_cad_gj",
        "mean_road_distance_km",
        "gt150_mean_road_distance_km",
        "gt300_mean_road_distance_km",
        "avoidable_distance_gt150_m3_km",
        "avoidable_distance_gt300_m3_km",
        "avoidable_cost_gt150_cad",
        "avoidable_cost_gt300_cad",
        "avoidable_cost_gt150_cad_gj",
        "local_capacity_utilization",
    }
    signed_fields = {
        "local_capacity_headroom_m3_y",
        "local_capacity_headroom_total_m3_y",
    }
    for path in (BANDS_CSV, GAPS_CSV, PROVINCES_CSV, PRIORITY_CSV):
        for row in _read_csv(path):
            for field, value in row.items():
                assert "nan" not in value.lower(), (path, field, value)
                assert "inf" not in value.lower(), (path, field, value)
                if field in numeric_fields:
                    number = float(value)
                    assert math.isfinite(number), (path, field)
                    assert number >= 0.0, (path, field)
                if field in signed_fields:
                    assert math.isfinite(float(value)), (path, field)


def test_capacity_totals_match_capacity_v3_layer(bands) -> None:
    offers = gap.read_offer_rows()
    capacity_pool = gap.load_capacity_pool(offers)
    for scenario in gap.SCENARIOS:
        total = math.fsum(
            float(row["eligible_local_capacity_m3_y"])
            for row in bands
            if row["scenario"] == scenario
        )
        assert total == pytest.approx(
            capacity_pool.total_capacity_m3_y, abs=1e-3
        )
    v8_capacity = {
        (row["province"], row["facility_class"], row["distance_band"]): float(
            row["eligible_local_capacity_m3_y"]
        )
        for row in bands
        if row["scenario"] == "v8"
    }
    v9_capacity = {
        (row["province"], row["facility_class"], row["distance_band"]): float(
            row["eligible_local_capacity_m3_y"]
        )
        for row in bands
        if row["scenario"] == "v9"
    }
    common = set(v8_capacity) & set(v9_capacity)
    assert {key: v8_capacity[key] for key in common} == {
        key: v9_capacity[key] for key in common
    }
    for key in set(v8_capacity) ^ set(v9_capacity):
        capacity = v8_capacity.get(key, v9_capacity.get(key, 0.0))
        assert capacity == 0.0, key


def test_priority_order_and_greenfield_summary(priority, summary) -> None:
    assert priority
    for scenario in gap.SCENARIOS:
        rows = [row for row in priority if row["scenario"] == scenario]
        assert rows
        ranks = [int(row["cost_rank"]) for row in rows]
        distance_ranks = [int(row["distance_rank"]) for row in rows]
        assert ranks == list(range(1, len(rows) + 1))
        assert sorted(distance_ranks) == list(range(1, len(rows) + 1))
        costs = [float(row["avoidable_cost_gt150_cad"]) for row in rows]
        assert all(
            costs[index] >= costs[index + 1] - 1e-6
            for index in range(len(costs) - 1)
        )
        for row in rows:
            assert float(row["assigned_gt150_m3_y"]) > 0.0
            assert float(row["avoidable_cost_gt150_cad"]) > 0.0
            assert float(row["assigned_gt300_m3_y"]) <= float(
                row["assigned_gt150_m3_y"]
            ) + 1e-3

    v9_summary_top = summary["top_greenfield_local_capacity"]["v9"]
    v9_rows = [row for row in priority if row["scenario"] == "v9"][:10]
    assert [row["province"] for row in v9_summary_top] == [
        row["province"] for row in v9_rows
    ]
    assert v9_rows[0]["province"] == "QC"
    assert v9_rows[0]["facility_class"] == "SAW"


def test_scenario_distance_and_cost_direction(summary) -> None:
    v8 = summary["scenarios"]["v8"]
    v9 = summary["scenarios"]["v9"]
    assert v9["mean_road_distance_km"] <= v8["mean_road_distance_km"]
    assert v9["transport_cost_cad"] <= v8["transport_cost_cad"]
    assert summary["validation"]["all_passed"] is True


def test_deletion_guard_and_no_product_residue_files() -> None:
    assert gap.parse_deletion_lines(" D file\nM  file\n?? untracked\n") == [
        " D file"
    ]
    assert gap.parse_deletion_lines("M  file\n?? untracked\n") == []
    gap.assert_no_unexpected_deletions()
    names = {path.name.lower() for path in OUTPUT_DIR.iterdir() if path.is_file()}
    assert not any("product" in name for name in names)
    assert not any("residue" in name for name in names)
