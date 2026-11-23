"""Contract tests for the read-only D1/D2 OSeCAN candidate cases v1."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.facilities import osecan_candidate_cases_v1 as cases

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/osecan_candidate_cases_v1"
)
REQUIRED_FILES = (
    "case_index.csv",
    "candidate_activity_limits.csv",
    "comparison_supply_curve.csv",
    "summary.json",
    "manifest.json",
    cases.REPORT_NAME,
)
requires_artifacts = pytest.mark.skipif(
    not all((ARTIFACT_DIR / name).exists() for name in REQUIRED_FILES),
    reason="osecan candidate cases v1 has not been generated",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    return _json(ARTIFACT_DIR / "summary.json")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _json(ARTIFACT_DIR / "manifest.json")


@pytest.fixture(scope="module")
def case_rows() -> list[dict[str, str]]:
    return _rows(ARTIFACT_DIR / "case_index.csv")


@pytest.fixture(scope="module")
def candidate_rows() -> list[dict[str, str]]:
    return _rows(ARTIFACT_DIR / "candidate_activity_limits.csv")


@pytest.fixture(scope="module")
def comparison_rows() -> list[dict[str, str]]:
    return _rows(ARTIFACT_DIR / "comparison_supply_curve.csv")


@requires_artifacts
def test_twelve_cases_and_artifact_status(
    summary: dict, manifest: dict, case_rows: list[dict[str, str]]
) -> None:
    assert summary["artifact"] == cases.ARTIFACT
    assert summary["artifact_version"] == cases.ARTIFACT_VERSION
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["record_counts"]["cases"] == 12
    assert len(case_rows) == 12
    assert manifest["status"] == "pass"
    assert manifest["validation"]["passed"] is True

    expected_ids = [
        f"r{recovery}_a{availability}"
        for recovery in ("035", "060", "075")
        for availability in ("000", "025", "050", "075")
    ]
    assert [row["case_id"] for row in case_rows] == expected_ids
    assert {
        (float(row["d1_recovery_fraction"]), float(row["d2_availability_fraction"]))
        for row in case_rows
    } == {
        (recovery, availability)
        for recovery in (0.35, 0.60, 0.75)
        for availability in (0.0, 0.25, 0.50, 0.75)
    }


@requires_artifacts
def test_candidate_zone_coverage_and_conservation(
    summary: dict,
    candidate_rows: list[dict[str, str]],
) -> None:
    assert len(candidate_rows) == 312
    handoff = _json(cases.HANDOFF_SUMMARY)
    for case_row in summary["case_index"]:
        case_id = case_row["case_id"]
        for stream, case_key in (
            ("process", case_row["d2_availability_case"]),
            ("harvest", case_row["d1_recovery_case"]),
        ):
            rows = [
                row
                for row in candidate_rows
                if row["case_id"] == case_id and row["stream"] == stream
            ]
            assert len(rows) == 13
            assert {row["zone_id"] for row in rows} == set(cases.ZONE_ORDER)
            assert sum(
                float(row["candidate_activity_limit_pj"]) for row in rows
            ) == pytest.approx(
                handoff["handoff_totals"][stream][case_key], abs=1e-6
            )
            assert all(
                row["technology_suggestion"] for row in rows
            )


@requires_artifacts
def test_streams_are_separate_and_zero_case_is_zero(
    candidate_rows: list[dict[str, str]],
) -> None:
    assert {row["stream"] for row in candidate_rows} == {"process", "harvest"}
    assert not any(
        "combined" in column or column == "total_pj"
        for column in candidate_rows[0]
    )
    zero_rows = [
        row
        for row in candidate_rows
        if row["stream"] == "process"
        and row["stream_case"] == "process_availability_000"
    ]
    assert len(zero_rows) == 3 * 13
    assert all(float(row["candidate_activity_limit_pj"]) == 0.0 for row in zero_rows)


@requires_artifacts
def test_labels_and_read_only_status(
    summary: dict, candidate_rows: list[dict[str, str]]
) -> None:
    labels = summary["labels"]
    assert labels["model_estimate"] is True
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["national_claim"] is False
    for row in candidate_rows:
        assert row["model_estimate"] == "true"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["official_osecan_changed"] == "false"
        assert row["candidate_status"] == cases.CANDIDATE_STATUS
        assert row["candidate_basis"]
        assert row["caveat"]


@requires_artifacts
def test_b1_b2_and_national_supply_curve_comparison(
    comparison_rows: list[dict[str, str]],
) -> None:
    assert len(comparison_rows) == 24
    for row in comparison_rows:
        case_pj = float(row["case_pj"])
        assert row["model_estimate"] == "true"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["official_osecan_changed"] == "false"
        assert row["reference_is_policy_target"] == "false"
        assert float(row["national_t4_upper_pj"]) == pytest.approx(
            213.538041, abs=1e-6
        )
        assert float(row["national_t4_ratio"]) == pytest.approx(
            case_pj / 213.538041, abs=1e-9
        )
        assert float(row["national_t4_headroom_pj"]) == pytest.approx(
            213.538041 - case_pj, abs=1e-9
        )
        assert float(row["b1_official_limit_pj"]) == pytest.approx(
            3.605474907, abs=1e-9
        )
        assert float(row["b2_official_limit_pj"]) == pytest.approx(
            39.918341125, abs=1e-9
        )
        b1 = float(row["b1_candidate_pj"])
        b2 = float(row["b2_candidate_pj"])
        assert float(row["bc_case_pj"]) == pytest.approx(b1 + b2, abs=1e-9)
        assert float(row["b1_ratio"]) == pytest.approx(b1 / 3.605474907, abs=1e-9)
        assert float(row["b2_ratio"]) == pytest.approx(b2 / 39.918341125, abs=1e-9)


@requires_artifacts
def test_no_official_file_change(summary: dict) -> None:
    osecan_integrity = summary["osecan_integrity"]
    assert osecan_integrity["unchanged"] is True
    assert osecan_integrity["changed_files"] == []
    assert osecan_integrity["tree_sha256_before"] == osecan_integrity[
        "tree_sha256_after"
    ]
    _, current_tree = cases._hash_tree(cases.OSECAN_DIR)
    assert current_tree == osecan_integrity["tree_sha256_after"]

    official = summary["official_schema_integrity"]
    assert official["unchanged"] is True
    assert official["changed_files"] == []
    for relative, payload in official["files"].items():
        assert payload["unchanged"] is True
        path = REPO_ROOT / relative
        assert cases._sha256(path) == payload["sha256_after"]

    output = cases.OUTPUT_DIR.resolve()
    assert cases.OSECAN_DIR.resolve() not in output.parents
    assert cases.OFFICIAL_MODEL_DIR.resolve() not in output.parents


@requires_artifacts
def test_manifest_hashes_match_outputs(manifest: dict, summary: dict) -> None:
    for name, payload in manifest["outputs"].items():
        path = ARTIFACT_DIR / name
        assert path.is_file()
        assert path.stat().st_size == payload["bytes"]
        assert cases._sha256(path) == payload["sha256"]
    for name, digest in summary["inputs"].items():
        assert cases._sha256(REPO_ROOT / name) == digest


@requires_artifacts
def test_report_documents_read_only_format() -> None:
    report = (ARTIFACT_DIR / cases.REPORT_NAME).read_text(encoding="utf-8")
    assert "12 D1 x D2 combinations" in report
    assert "candidate_activity_limits.csv" in report
    assert cases.CANDIDATE_STATUS in report
    assert "Process and harvest residue are separate streams" in report
    assert "B1/B2 official 2050 activity limits" in report
    assert "national supply curve" in report.lower()
    assert "No official OSeCAN input is written or modified" in report


def test_build_to_temporary_directory(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    built = cases.build(output_dir=artifact)
    assert built["status"] == "pass"
    assert built["validation"]["passed"] is True
    assert built["record_counts"]["cases"] == 12
    assert len(_rows(artifact / "case_index.csv")) == 12
    assert len(_rows(artifact / "candidate_activity_limits.csv")) == 312
    assert len(_rows(artifact / "comparison_supply_curve.csv")) == 24
    assert (artifact / cases.REPORT_NAME).is_file()
    assert (artifact / "summary.json").is_file()
    assert (artifact / "manifest.json").is_file()
    assert built["osecan_integrity"]["unchanged"] is True
    assert built["official_schema_integrity"]["unchanged"] is True
