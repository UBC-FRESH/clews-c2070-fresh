"""Contract tests for the MAGPlot Tier-1 measured tree lists.

The tests read the committed artifacts instead of rebuilding the 1.1 million
row extract.  They cover schema, counts, expansion, licence/provenance and the
no-NaN/no-negative contract, plus the QC MAGPlot/PEP overlap record.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.magplot_tier1_v0 import (
    DEFAULT_OUTPUT_DIR,
    SOURCE_SHA256,
    TIER1_SOURCES,
    TREE_LIST_COLUMNS,
)

REQUIRED_ARTIFACTS = (
    "qc_tree_list.csv.gz",
    "nb_tree_list.csv.gz",
    "yt_tree_list.csv.gz",
    "schema.csv",
    "coverage.csv",
    "sources.csv",
    "qc_pep_overlap.csv",
    "qc_pep_comparison.csv",
    "summary.json",
    "report.md",
    "manifest.json",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="MAGPlot Tier-1 artifacts have not been generated",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def artifact() -> dict[str, object]:
    summary = json.loads(
        (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (DEFAULT_OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    coverage = pd.read_csv(DEFAULT_OUTPUT_DIR / "coverage.csv")
    trees = {
        province: pd.read_csv(
            DEFAULT_OUTPUT_DIR / f"{province.lower()}_tree_list.csv.gz",
            dtype={"species_v": str},
            compression="gzip",
        )
        for province in TIER1_SOURCES
    }
    return {
        "dir": DEFAULT_OUTPUT_DIR,
        "summary": summary,
        "manifest": manifest,
        "coverage": coverage,
        "trees": trees,
        "schema": pd.read_csv(DEFAULT_OUTPUT_DIR / "schema.csv"),
        "sources": pd.read_csv(DEFAULT_OUTPUT_DIR / "sources.csv"),
        "overlap": pd.read_csv(DEFAULT_OUTPUT_DIR / "qc_pep_overlap.csv"),
        "comparison": pd.read_csv(DEFAULT_OUTPUT_DIR / "qc_pep_comparison.csv"),
    }


@requires_artifacts
def test_required_artifacts_exist() -> None:
    for name in REQUIRED_ARTIFACTS:
        path = DEFAULT_OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size > 0, name


@requires_artifacts
def test_tree_list_schema(artifact: dict[str, object]) -> None:
    schema = artifact["schema"]
    assert list(schema["column"]) == TREE_LIST_COLUMNS
    for province, frame in artifact["trees"].items():
        assert list(frame.columns) == TREE_LIST_COLUMNS, province
        assert set(frame["jurisdiction"]) == {province}
        assert set(frame["source_program"]) == {TIER1_SOURCES[province]}
        assert set(frame["plot_type_group"]) <= {"large", "small"}
        assert set(frame["dbh_basis"]) <= {"measured", "estimated", "unknown"}
        assert set(frame["height_basis"]) <= {
            "measured",
            "estimated",
            "calculated",
            "missing",
        }
        assert set(frame["tree_status"]) <= {
            "live_standing",
            "live",
            "live_fallen",
            "dead_standing",
            "unknown",
        }


@requires_artifacts
def test_counts_match_summary_and_coverage(artifact: dict[str, object]) -> None:
    coverage = artifact["coverage"].set_index("jurisdiction")
    summary = artifact["summary"]
    for province, frame in artifact["trees"].items():
        assert len(frame) == int(coverage.loc[province, "included_tree_rows"])
        assert frame["tree_record_id"].is_unique
        assert frame["magp_site_id"].nunique() == int(
            coverage.loc[province, "included_sites"]
        )
        assert frame["plot_id"].nunique() == int(
            coverage.loc[province, "included_plots"]
        )
        assert int(frame["live_standing"].sum()) == int(
            coverage.loc[province, "included_live_standing_rows"]
        )
        assert int(frame["tracked_tree"].sum()) == int(
            coverage.loc[province, "included_tracked_tree_rows"]
        )
    assert int(summary["totals"]["included_tree_rows"]) == int(
        sum(len(frame) for frame in artifact["trees"].values())
    )


@requires_artifacts
def test_expansion_and_plot_area(artifact: dict[str, object]) -> None:
    coverage = artifact["coverage"].set_index("jurisdiction")
    for province, frame in artifact["trees"].items():
        assert (frame["expansion_factor_stems_ha"] > 0).all()
        assert (frame["plot_area_ha"] > 0).all()
        implied = 1.0 / frame["plot_area_ha"]
        mismatch = (
            (frame["expansion_factor_stems_ha"] - implied).abs()
            > 0.01 * implied
        )
        assert int(mismatch.sum()) == int(
            coverage.loc[province, "included_expansion_area_mismatch_rows"]
        )
        expected_ba = np.pi * (frame["dbh_cm"] / 200.0) ** 2
        assert np.allclose(
            frame["basal_area_m2"], expected_ba, atol=1e-6, rtol=0
        )
        assert np.allclose(
            frame["basal_area_m2_ha"],
            frame["basal_area_m2"] * frame["expansion_factor_stems_ha"],
            atol=1e-5,
            rtol=0,
        )


@requires_artifacts
def test_no_nan_or_negative_numeric_values(artifact: dict[str, object]) -> None:
    required = [
        "dbh_cm",
        "expansion_factor_stems_ha",
        "plot_area_ha",
        "basal_area_m2",
        "basal_area_m2_ha",
        "tree_num",
        "meas_num",
        "meas_year",
    ]
    for province, frame in artifact["trees"].items():
        assert not frame[required].isna().any().any(), province
        assert (frame[required] >= 0).all().all(), province
        assert (frame["dbh_cm"] > 0).all(), province
        assert (frame["expansion_factor_stems_ha"] > 0).all(), province
        assert (frame["plot_area_ha"] > 0).all(), province
        assert (frame["tree_num"] > 0).all(), province
        assert frame["meas_year"].between(1900, 2026).all(), province
        assert frame["height_m"].dropna().gt(0).all(), province
        for column in ["height_m", "taglimit_dbh_cm", "taglimit_height_m"]:
            assert not frame[column].isin([-1, -9]).any(), (province, column)


@requires_artifacts
def test_labels_and_provenance(artifact: dict[str, object]) -> None:
    for province, frame in artifact["trees"].items():
        assert frame["measured_tree_list"].all()
        assert frame["open_data"].all()
        assert not frame["model_estimate"].any()
        assert not frame["national_claim"].any()
        assert set(frame["licence"]) == {"OGL-Canada"}
        assert set(frame["access_tier"]) == {1}
        assert set(frame["provenance_id"]) == {
            f"magplot_v1.2_tier1_{TIER1_SOURCES[province].lower()}_latest_visit"
        }
    sources = artifact["sources"].set_index("source_program")
    for source in TIER1_SOURCES.values():
        row = sources.loc[source]
        assert row["sha256"] == SOURCE_SHA256
        assert row["access_tier"] == 1
        assert "Open Government Licence" in row["licence"]
        assert str(row["source_url"]).startswith("https://ca.nfis.org/")
        assert row["retrieved_utc"]
    summary = artifact["summary"]
    assert summary["tier2_or_tier3_used"] is False
    assert summary["labels"]["bucking_performed"] is False
    assert summary["labels"]["national_claim"] is False


@requires_artifacts
def test_qc_pep_overlap(artifact: dict[str, object]) -> None:
    comparison = artifact["comparison"].set_index("metric")
    summary = artifact["summary"]["qc_pep"]
    assert int(comparison.loc["matched_sites", "value"]) >= 12_800
    assert float(comparison.loc["matched_site_share", "value"]) >= 0.999
    assert float(comparison.loc["dbh_within_0_1_cm_share", "value"]) >= 0.999
    assert float(comparison.loc["expansion_exact_share", "value"]) >= 0.999
    assert int(comparison.loc["pep_vmb_tige_nonnull", "value"]) == 0
    assert int(comparison.loc["pep_vmb_ha_nonnull", "value"]) == 0
    assert int(summary["matched_sites"]) == int(
        comparison.loc["matched_sites", "value"]
    )
    overlap = artifact["overlap"]
    assert len(overlap) == int(summary["magp_qc_psp_sites"])
    assert overlap["pep_matched"].sum() == int(summary["matched_sites"])


@requires_artifacts
def test_manifest_hashes_and_rows(artifact: dict[str, object]) -> None:
    manifest = artifact["manifest"]
    assert manifest["source"]["sha256"] == SOURCE_SHA256
    assert manifest["source"]["expected_sha256"] == SOURCE_SHA256
    labels = {record["label"] for record in manifest["artifacts"]}
    for name in REQUIRED_ARTIFACTS:
        if name == "manifest.json":
            continue
        assert name in labels
    for record in manifest["artifacts"]:
        path = DEFAULT_OUTPUT_DIR / str(record["label"])
        assert sha256(path) == record["sha256"], record["label"]
        if record["rows"] is not None:
            assert int(record["rows"]) >= 1
