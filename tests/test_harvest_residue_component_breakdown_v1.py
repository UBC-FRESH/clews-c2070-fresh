"""Regression tests for the harvest-residue component breakdown v1."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    harvest_residue_component_breakdown_v1 as breakdown,
)

OUTPUT_DIR = breakdown.OUTPUT_DIR
COMPONENT_CSV = OUTPUT_DIR / "component_by_province.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
PROVENANCE_CSV = OUTPUT_DIR / "provenance.csv"
REPORT_MD = OUTPUT_DIR / breakdown.REPORT_NAME


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def manifest() -> dict:
    with MANIFEST_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def component_rows() -> list[dict[str, str]]:
    return _read_csv(COMPONENT_CSV)


@pytest.fixture(scope="module")
def chain_rows() -> dict[str, dict[str, str]]:
    return {
        row["province"]: row
        for row in _read_csv(breakdown.CHAIN_CSV)
    }


@pytest.fixture(scope="module")
def provenance_rows() -> list[dict[str, str]]:
    return _read_csv(PROVENANCE_CSV)


def test_outputs_fields_manifest_and_labels(
    summary, manifest, component_rows
) -> None:
    for path in (
        COMPONENT_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        PROVENANCE_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(component_rows[0]) == list(breakdown.COMPONENT_FIELDS)
    assert manifest["artifact"] == breakdown.ARTIFACT
    assert manifest["artifact_version"] == breakdown.ARTIFACT_VERSION
    assert manifest["status"] == "complete"
    assert manifest["labels"] == summary["labels"]
    assert summary["status"] == "pass"
    assert summary["validation"]["all_passed"] is True
    assert not summary["validation"]["failures"]
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert breakdown._sha256(path) == entry["sha256"], name
    for relative, expected_hash in manifest["inputs"].items():
        path = breakdown.REPO_ROOT / relative
        assert path.is_file(), relative
        assert breakdown._sha256(path) == expected_hash, relative


def test_rows_labels_tiers_and_tops(component_rows) -> None:
    assert len(component_rows) == len(breakdown.PROVINCES)
    assert {row["province"] for row in component_rows} == set(
        breakdown.PROVINCES
    )
    for row in component_rows:
        assert row["tier"] == breakdown.TIER_BY_PROVINCE[row["province"]]
        assert row["tops_separately_modeled"] == "false"
        assert "not separately modelled" in row["tops_note"]
        assert row["model_estimate"] == "true"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["national_claim"] == "false"
        assert row["official_osecan_changed"] == "false"
        assert row["ui_changed"] == "false"
        if float(row["total_odt"]) > 0.0:
            shares = (
                float(row["stem_share"])
                + float(row["branch_share"])
                + float(row["foliage_share"])
            )
            assert math.isclose(shares, 1.0, abs_tol=5e-9)
            assert math.isclose(
                float(row["branch_foliage_share"]),
                float(row["branch_share"]) + float(row["foliage_share"]),
                abs_tol=5e-9,
            )


def test_component_conservation_and_conversion(
    component_rows, chain_rows
) -> None:
    for row in component_rows:
        province = row["province"]
        chain = chain_rows[province]
        stem_odt = float(row["stem_odt"])
        branch_odt = float(row["branch_odt"])
        foliage_odt = float(row["foliage_odt"])
        total_odt = float(row["total_odt"])
        chain_total = float(chain["harvest_residue_total_gross_odt"])
        chain_branch_foliage = float(
            chain["harvest_residue_branch_foliage_odt"]
        )
        assert math.isclose(stem_odt, float(chain["harvest_residue_stem_gross_odt"]), abs_tol=1e-6)
        assert math.isclose(total_odt, chain_total, abs_tol=1e-6)
        assert abs(stem_odt + branch_odt + foliage_odt - chain_total) <= 1e-4
        assert math.isclose(
            branch_odt + foliage_odt, chain_branch_foliage, abs_tol=1e-6
        )
        for odt_field, pj_field in (
            ("stem_odt", "stem_pj"),
            ("branch_odt", "branch_pj"),
            ("foliage_odt", "foliage_pj"),
            ("total_odt", "total_pj"),
        ):
            expected_pj = (
                float(row[odt_field]) * breakdown.GJ_PER_ODT * 1e-6
            )
            assert math.isclose(
                float(row[pj_field]), expected_pj, abs_tol=1e-6
            )
        assert math.isclose(
            float(row["branch_foliage_pj"]),
            (branch_odt + foliage_odt) * breakdown.GJ_PER_ODT * 1e-6,
            abs_tol=1e-6,
        )


def test_branch_ratio_matches_source(component_rows) -> None:
    e1 = {
        row["province"]: row
        for row in _read_csv(breakdown.E1_CSV)
    }
    e2c = {
        row["province"]: row
        for row in _read_csv(breakdown.E2C_CSV)
    }
    for row in component_rows:
        province = row["province"]
        source = e1.get(province) or e2c[province]
        branch = float(source["branch_odt_expanded"])
        foliage = float(source["foliage_odt_expanded"])
        expected_ratio = branch / (branch + foliage)
        assert math.isclose(
            float(row["branch_share_of_branch_foliage"]),
            expected_ratio,
            abs_tol=1e-9,
        )


def test_national_totals_and_recovery(summary, component_rows) -> None:
    total_pj = math.fsum(float(row["total_pj"]) for row in component_rows)
    branch_foliage_pj = math.fsum(
        float(row["branch_foliage_pj"]) for row in component_rows
    )
    assert math.isclose(
        total_pj, breakdown.EXPECTED_NATIONAL_TOTAL_PJ, abs_tol=1e-5
    )
    assert math.isclose(
        branch_foliage_pj,
        breakdown.EXPECTED_BRANCH_FOLIAGE_PJ,
        abs_tol=1e-3,
    )
    totals = summary["national_totals"]
    assert totals["total_pj_display"] == 444.411
    assert totals["branch_foliage_pj_display"] == 316.579
    assert math.isclose(
        totals["total_pj"],
        breakdown.EXPECTED_NATIONAL_TOTAL_PJ,
        abs_tol=breakdown.NATIONAL_TOTAL_TOLERANCE_PJ,
    )
    assert math.isclose(
        totals["stem_share"]
        + totals["branch_share"]
        + totals["foliage_share"],
        1.0,
        abs_tol=1e-12,
    )
    recovery = summary["recovery"]
    assert recovery["denominator"] == "harvest_residue_total_gross_odt"
    assert math.isclose(
        recovery["denominator_pj"], totals["total_pj"], abs_tol=1e-12
    )
    assert recovery["calibrated"] is False
    assert recovery[
        "environmental_or_technical_constraint_modeled"
    ] is False
    assert math.isclose(
        recovery["recoverable_pj"]["0.60"],
        breakdown.EXPECTED_RECOVERABLE_060_PJ,
        abs_tol=1e-3,
    )
    assert recovery["recoverable_pj_display"] == {
        "0.35": 155.544,
        "0.60": 266.646,
        "0.75": 333.308,
    }


def test_model_card_cross_check(summary) -> None:
    cross_check = summary["model_card_cross_check"]
    assert cross_check["tolerance_pj"] == 0.001
    assert cross_check["all_within_tolerance"] is True
    assert len(cross_check["provinces"]) == len(breakdown.PROVINCES)
    for entry in cross_check["provinces"]:
        assert abs(entry["delta_pj"]) <= 0.001
        assert entry["within_tolerance"] is True
    assert summary["validation"]["checks"]["model_card_5_3_cross_check"] is True


def test_labels_and_scope(summary) -> None:
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
    }
    assert "no environmental" in " ".join(summary["caveats"]).lower()
    assert "tops" in " ".join(summary["caveats"]).lower()


def test_provenance_inputs(provenance_rows, manifest) -> None:
    assert len(provenance_rows) == 6
    by_id = {row["source_id"]: row for row in provenance_rows}
    for source_id in (
        "national_product_chain_v5",
        "four_province_bucking_pilot_v0",
        "open_data_imputation_v3",
        "model_card_national_facility_chain",
    ):
        assert source_id in by_id
        assert by_id[source_id]["sha256"] == manifest["inputs"][
            by_id[source_id]["path"]
        ]
    assert by_id["component_breakdown_arithmetic"]["role"] == (
        "derived_parameter"
    )
    assert by_id["cfs_branch_foliage_equations"]["role"] == "context"


def test_deletion_guard() -> None:
    assert breakdown.parse_deletion_lines(
        " D file\nM  file\n?? untracked\n"
    ) == [" D file"]
    assert breakdown.parse_deletion_lines(
        "M  file\n?? untracked\n"
    ) == []
    breakdown.assert_no_unexpected_deletions()

    def fake_status(repository: Path) -> str:
        return "D  deleted.csv\n"

    with pytest.raises(
        breakdown.ComponentBreakdownError, match="unexpected deletion"
    ):
        breakdown.assert_no_unexpected_deletions(
            (Path("/tmp/fake-repo"),), status_reader=fake_status
        )


def test_build_into_temporary_directory(tmp_path: Path) -> None:
    result = breakdown.build(output_dir=tmp_path)
    assert result["status"] == "pass"
    assert result["validation"]["all_passed"] is True
    for name in (
        "component_by_province.csv",
        "summary.json",
        "provenance.csv",
        breakdown.REPORT_NAME,
        "manifest.json",
    ):
        assert (tmp_path / name).is_file(), name
