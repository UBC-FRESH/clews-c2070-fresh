"""Contract tests for the v4 energy decision-impact matrix v1."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.facilities import energy_decision_matrix_v1 as matrix

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = REPO_ROOT / "data/derived/national/facilities/energy_decision_matrix_v1"
DOCS_PATH = REPO_ROOT / "docs/energy-decision-matrix.md"


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"artifact not generated: {path}")
    return path


def _json(path: Path) -> dict:
    return json.loads(_require(path).read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    with _require(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    return _json(ARTIFACT_DIR / "summary.json")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _json(ARTIFACT_DIR / "manifest.json")


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _rows(ARTIFACT_DIR / "decision_matrix.csv")


def test_artifact_status_and_counts(summary: dict, manifest: dict) -> None:
    assert summary["artifact"] == matrix.ARTIFACT
    assert summary["artifact_version"] == matrix.ARTIFACT_VERSION
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["record_counts"] == {
        "decisions": 4,
        "numeric_decisions": 2,
        "qualitative_decisions": 2,
        "matrix_rows": 9,
        "numeric_rows": 7,
        "qualitative_rows": 2,
    }
    assert manifest["artifact"] == matrix.ARTIFACT
    assert manifest["status"] == "pass"
    assert manifest["validation"]["passed"] is True


def test_d1_and_d2_reconcile_to_handoff(
    summary: dict, rows: list[dict[str, str]]
) -> None:
    numeric = [row for row in rows if row["numeric_basis"] != "not_quantified"]
    assert len(numeric) == 7

    expected = {
        ("D1", "harvest_recovery_035"): (155.5437681267482, "T4"),
        ("D1", "harvest_recovery_060"): (266.6464596457938, "above_T4"),
        ("D1", "harvest_recovery_075"): (333.3080745574168, "above_T4"),
        ("D2", "process_availability_000"): (0.0, "T1"),
        ("D2", "process_availability_025"): (124.6877292302247, "T3"),
        ("D2", "process_availability_050"): (249.3754584602354, "above_T4"),
        ("D2", "process_availability_075"): (374.0631876904527, "above_T4"),
    }
    for row in numeric:
        key = (row["decision_id"], row["case_key"])
        value, tier = expected[key]
        assert float(row["value_pj"]) == pytest.approx(value, abs=1e-6)
        assert row["osecan_tier"] == tier
        assert row["model_estimate"] == "true"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["national_claim"] == "false"
        assert row["official_osecan_changed"] == "false"

    d1_total = sum(
        float(row["value_pj"]) for row in rows if row["decision_id"] == "D1"
    )
    assert d1_total == pytest.approx(755.4983023300, abs=1e-6)
    assert summary["tier_thresholds"]["T4"] == pytest.approx(213.538041)


def test_qualitative_decisions_have_no_invented_numbers(
    rows: list[dict[str, str]]
) -> None:
    qualitative = [
        row for row in rows if row["numeric_basis"] == "not_quantified"
    ]
    assert [row["decision_id"] for row in qualitative] == ["D3", "D4"]
    for row in qualitative:
        assert row["value_pj"] == ""
        assert row["osecan_tier"] == ""
        assert row["unit"] == ""
        assert row["no_numbers_invented"].lower() == "true"
        assert row["context_reference"]
        assert len(row["qualitative_implication"]) > 100
        assert row["model_estimate"] == "true"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["national_claim"] == "false"
        assert row["official_osecan_changed"] == "false"


def test_screening_defaults_are_not_policy(summary: dict) -> None:
    decisions = {item["decision_id"]: item for item in summary["decisions"]}
    assert decisions["D1"]["default_case"] == "harvest_recovery_060"
    assert decisions["D1"]["default_role"] == "screening_reference_not_policy"
    assert decisions["D2"]["default_case"] == "process_availability_025"
    assert decisions["D2"]["default_role"] == (
        "screening_conservative_not_policy"
    )
    assert decisions["D3"]["no_numbers_invented"] is True
    assert decisions["D4"]["no_numbers_invented"] is True
    assert "not calibrated" in decisions["D1"]["recommended_default"]
    assert "not an availability estimate" in decisions["D2"]["recommended_default"]


def test_labels_and_non_claims(summary: dict) -> None:
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    joined = " ".join(summary["non_claims"]).lower()
    assert "not policy grade" in joined
    assert "not an official osecan result" in joined
    assert "no official osecan file" in joined
    assert "no non-forestry feedstock or industrial demand quantity" in joined


def test_report_and_docs_are_present() -> None:
    report = _require(ARTIFACT_DIR / matrix.REPORT_NAME).read_text(
        encoding="utf-8"
    )
    docs = _require(DOCS_PATH).read_text(encoding="utf-8")
    assert "Energy decision-impact matrix v1" in report
    assert "D1 - Harvest recovery basis" in report
    assert "D2 - Process-residue availability" in report
    assert "155.544" in report and "124.688" in report
    assert "not policy" in docs.lower()
    assert "Rubino" in docs and "Pehl" in docs
    assert "no number is invented" in docs.lower()


def test_manifest_hashes_match_outputs(manifest: dict, summary: dict) -> None:
    for name, digest in manifest["outputs"].items():
        path = ARTIFACT_DIR / name
        assert path.stat().st_size == digest["bytes"]
        assert matrix._sha256(path) == digest["sha256"]
    for name, digest in summary["inputs"].items():
        path = REPO_ROOT / name
        assert matrix._sha256(path) == digest


def test_build_to_temporary_directory(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    docs = tmp_path / "energy-decision-matrix.md"
    built = matrix.build(output_dir=artifact, docs_path=docs)
    assert built["status"] == "pass"
    assert (artifact / "decision_matrix.csv").is_file()
    assert (artifact / matrix.REPORT_NAME).is_file()
    assert (artifact / "summary.json").is_file()
    assert (artifact / "manifest.json").is_file()
    assert docs.is_file()
    assert len(_rows(artifact / "decision_matrix.csv")) == 9
