"""Tests for national evidence-tier coverage and replacement queue."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import national_tier_coverage as coverage

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("national_tier_coverage")
    summary = coverage.build(out_dir)
    return out_dir, summary


def _expected_reported_share() -> float:
    volumes = coverage.load_allocation_volumes()
    reported_cells = (
        2 * volumes["BC"]  # BC price + BC silviculture
        + volumes["QC"]  # QC road
    )
    denominator = sum(volumes.values()) * len(coverage.COMPONENTS)
    return reported_cells / denominator


def test_all_thirteen_jurisdictions_and_five_components(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / coverage.COVERAGE_CSV)
    assert len(rows) == 65
    assert summary["coverage"]["province_count"] == 13
    assert summary["coverage"]["component_count"] == 5
    assert summary["coverage"]["expected_cells"] == 65
    assert summary["coverage"]["covered_cells"] == 65
    assert summary["coverage"]["no_gap_count"] == 65
    assert summary["coverage"]["silent_gap_count"] == 0
    assert summary["coverage"]["missing_cells"] == []
    assert {row["province"] for row in rows} == set(coverage.PROVINCES)
    assert {row["component"] for row in rows} == set(coverage.COMPONENTS)
    assert len({(row["province"], row["component"]) for row in rows}) == 65


def test_tier_counts_and_shares(built):
    _, summary = built
    assert summary["tier_counts"] == {
        "reported_average": 3,
        "proxy_model": 2,
        "donor_proxy": 60,
        "assumption": 0,
    }
    assert math.isclose(
        sum(summary["tier_shares"].values()), 1.0, rel_tol=0, abs_tol=1e-12
    )
    for tier, count in summary["tier_counts"].items():
        assert summary["tier_shares"][tier] == pytest.approx(count / 65)
    component = summary["component_tier_counts"]
    assert component["price"]["reported_average"] == 1
    assert component["price"]["donor_proxy"] == 12
    assert component["logging"]["proxy_model"] == 1
    assert component["logging"]["donor_proxy"] == 12
    assert component["haul"]["proxy_model"] == 1
    assert component["haul"]["donor_proxy"] == 12
    assert component["road"]["reported_average"] == 1
    assert component["road"]["donor_proxy"] == 12
    assert component["silviculture"]["reported_average"] == 1
    assert component["silviculture"]["donor_proxy"] == 12
    for component_counts in component.values():
        assert component_counts["assumption"] == 0


def test_cells_are_labelled_and_sources_resolve(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / coverage.COVERAGE_CSV)
    sources = {
        row["source_key"]: row
        for row in _read_csv(
            REPO_ROOT
            / "data/derived/national/harvest_basis/national_economics_v1/sources.csv"
        )
    }
    for row in rows:
        assert row["tier"] in coverage.TIER_SCORE
        assert int(row["tier_score"]) == coverage.TIER_SCORE[row["tier"]]
        assert row["source_id"] in sources
        assert row["source_evidence_tier"] == sources[row["source_id"]][
            "evidence_tier"
        ]
        assert row["direct_evidence"] == (
            "true" if row["tier"] == "reported_average" else "false"
        )
        assert row["donor_or_assumption"] == (
            "true"
            if row["tier"] in {"donor_proxy", "assumption"}
            else "false"
        )
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "model_estimate"
        assert row["combined_national_net_value"] == "false"
        assert row["strict_open"] == "false"
        assert row["gap_status"] == "covered_explicit_tier"
        assert float(row["harvest_volume_m3"]) >= 0.0
        assert "not_estimated" not in " ".join(row.values()).lower()
    for row in rows:
        if row["tier"] == "donor_proxy":
            assert row["donor_province"]
            assert row["replacement_queue_eligible"] == "true"
    bc_price = next(
        row
        for row in rows
        if row["province"] == "BC" and row["component"] == "price"
    )
    assert bc_price["tier"] == "reported_average"
    assert bc_price["direct_evidence"] == "true"
    assert bc_price["donor_province"] == ""


def test_volume_weighted_coverage(built):
    _, summary = built
    weighted = summary["volume_weighted"]
    assert weighted["national_volume_m3"] == pytest.approx(
        118_977_339.1, abs=1.0
    )
    assert weighted["volume_weighted_reported_share"] == pytest.approx(
        _expected_reported_share(), abs=1e-10
    )
    assert (
        weighted["volume_weighted_reported_share"]
        == weighted["volume_weighted_direct_or_reported_share"]
        == weighted["volume_weighted_direct_share"]
    )
    assert weighted["volume_weighted_reported_share"] < 0.20
    total = sum(weighted["tier_share"].values())
    assert total == pytest.approx(1.0, abs=1e-10)
    assert 0.0 < weighted["volume_weighted_tier_score"] <= 4.0
    assert weighted["volume_weighted_evidence_ratio"] == pytest.approx(
        weighted["volume_weighted_tier_score"] / 4.0
    )


def test_donor_provinces_are_not_relabelled(built):
    _, summary = built
    donors = summary["donor_provinces"]
    assert donors["donor_provinces"] == ["BC", "QC"]
    assert donors["donor_province_count"] == 2
    assert donors["by_donor_province"]["BC"]["cell_count"] == 48
    assert donors["by_donor_province"]["BC"]["components"] == {
        "price": 12,
        "logging": 12,
        "haul": 12,
        "road": 0,
        "silviculture": 12,
    }
    assert donors["by_donor_province"]["QC"]["cell_count"] == 12
    assert donors["by_donor_province"]["QC"]["components"] == {
        "price": 0,
        "logging": 0,
        "haul": 0,
        "road": 12,
        "silviculture": 0,
    }


def test_replacement_queue_order_and_categories(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / coverage.QUEUE_CSV)
    assert len(rows) == 60
    assert summary["replacement_queue"]["row_count"] == 60
    assert [int(row["queue_rank"]) for row in rows] == list(range(1, 61))
    assert [(row["province"], row["component"]) for row in rows[:6]] == [
        ("BC", "road"),
        ("QC", "price"),
        ("QC", "logging"),
        ("QC", "haul"),
        ("QC", "silviculture"),
        ("AB", "price"),
    ]

    volumes = [float(row["harvest_volume_m3"]) for row in rows]
    assert volumes == sorted(volumes, reverse=True)
    priority_rank = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
    priorities = [priority_rank[row["priority"]] for row in rows]
    assert priorities == sorted(priorities)
    assert all(row["tier"] in {"donor_proxy", "assumption"} for row in rows)
    assert all(row["queue_status"] == "open_required_source_not_invented" for row in rows)
    for row in rows:
        expected_category = coverage.REQUIRED_SOURCE_CATEGORY[row["component"]]
        assert row["required_source_category"] == expected_category
    assert rows[-1]["province"] == "NU"
    assert rows[-1]["priority"] == "P4"
    assert summary["replacement_queue"]["second_wave_proxy_model_cells"] == 2
    assert set(summary["replacement_queue"]["second_wave_components"]) == {
        "BC:logging",
        "BC:haul",
    }


def test_summary_and_report_preserve_national_label(built):
    out_dir, summary = built
    assert summary["national_diagnostic_label"] == "model_estimate_not_policy"
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["combined_national_net_value"] is False
    assert summary["validation"]["no_margin_recalculation"] is True
    assert summary["validation"]["no_silent_gaps"] is True
    assert summary["validation"]["queue_only_donor_or_assumption"] is True
    report = (out_dir / coverage.REPORT_MD).read_text(encoding="utf-8")
    assert "0 silent gaps" in report
    assert "model_estimate_not_policy" in report
    assert "P1" in report
    assert "donor" in report.lower()


def test_manifest_hashes_outputs_and_inputs(built):
    out_dir, _ = built
    manifest = json.loads(
        (out_dir / coverage.MANIFEST_JSON).read_text(encoding="utf-8")
    )
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["coverage"]["silent_gap_count"] == 0
    assert len(manifest["inputs"]) == 8
    assert len(manifest["outputs"]) == 4
    for item in manifest["inputs"] + manifest["outputs"]:
        assert len(item["sha256"]) == 64
        assert item["bytes"] > 0
        assert not any(
            marker in item["path"].lower()
            for marker in ("confidential", "restricted", "microdata")
        )


def test_no_not_estimated_in_outputs(built):
    out_dir, _ = built
    for filename in (
        coverage.COVERAGE_CSV,
        coverage.QUEUE_CSV,
        coverage.SUMMARY_JSON,
        coverage.REPORT_MD,
    ):
        text = (out_dir / filename).read_text(encoding="utf-8").lower()
        assert "not_estimated" not in text


def test_deletion_parser_fails_closed():
    status = " M changed.csv\nD  deleted.csv\n D also_deleted.csv\n?? new.csv\n"
    assert coverage._deletion_lines(status) == [
        "D  deleted.csv",
        " D also_deleted.csv",
    ]
    assert coverage._deletion_lines(" M changed.csv\n?? new.csv\n") == []
