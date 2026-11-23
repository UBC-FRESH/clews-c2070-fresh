"""Contract tests for the national taper coverage register v1.

The register is a model-estimate coverage audit, not a supply estimate.  These
tests enforce the contract that matters for auditability: all 13 jurisdictions,
shares that sum to one, no NaN/negative outputs, explicit local/donor/proxy
states, gap reasons for every unmapped row, and provenance hashes.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.national_taper_coverage import (
    DEFAULT_OUTPUT_DIR,
    PROVINCES,
)

REQUIRED_ARTIFACTS = (
    "coverage.csv",
    "province_summary.csv",
    "gaps.csv",
    "summary.json",
    "report.md",
    "manifest.json",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="national taper coverage register has not been generated",
)


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def artifact() -> dict[str, object]:
    return {
        "dir": DEFAULT_OUTPUT_DIR,
        "summary": json.loads(
            (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
        ),
        "manifest": json.loads(
            (DEFAULT_OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
        ),
        "coverage": pd.read_csv(DEFAULT_OUTPUT_DIR / "coverage.csv"),
        "province": pd.read_csv(DEFAULT_OUTPUT_DIR / "province_summary.csv"),
        "gaps": pd.read_csv(DEFAULT_OUTPUT_DIR / "gaps.csv"),
        "report": (DEFAULT_OUTPUT_DIR / "report.md").read_text(encoding="utf-8"),
    }


@requires_artifacts
def test_all_13_jurisdictions_are_present(artifact) -> None:
    coverage = artifact["coverage"]
    province = artifact["province"]
    summary = artifact["summary"]

    assert set(province["province"]) == set(PROVINCES)
    assert len(province) == 13
    assert set(coverage["province"]) == set(PROVINCES)
    assert summary["counts"]["jurisdictions"] == 13
    assert coverage["province"].nunique() == 13
    assert coverage.groupby("province").size().ge(4).all()


@requires_artifacts
def test_coverage_shares_sum_to_one(artifact) -> None:
    coverage = artifact["coverage"]
    province = artifact["province"]

    stem_sums = coverage.groupby("province")["eligible_stem_share"].sum()
    volume_sums = coverage.groupby("province")["eligible_volume_share"].sum()
    assert np.allclose(stem_sums.to_numpy(), 1.0, atol=1e-8)
    assert np.allclose(volume_sums.to_numpy(), 1.0, atol=1e-8)

    for prefix in ("stem", "volume"):
        total = (
            province[f"local_{prefix}_pct"]
            + province[f"donor_{prefix}_pct"]
            + province[f"unmapped_{prefix}_pct"]
        )
        assert np.allclose(total.to_numpy(), 100.0, atol=1e-6)
    assert np.allclose(
        (
            province["local_volume_pct"]
            + province["donor_volume_pct"]
            - province["mapped_volume_pct"]
        ).to_numpy(),
        0.0,
        atol=1e-8,
    )
    assert np.allclose(
        (
            province["local_stem_pct"]
            + province["donor_stem_pct"]
            + province["unmapped_stem_pct"]
        ).to_numpy(),
        100.0,
        atol=1e-6,
    )


@requires_artifacts
def test_no_nan_or_negative_values(artifact) -> None:
    coverage = artifact["coverage"]
    province = artifact["province"]
    gaps = artifact["gaps"]

    for frame in (coverage, province, gaps):
        assert not frame.isna().any().any()
    for column in (
        "eligible_stems",
        "eligible_stem_share",
        "eligible_volume_m3",
        "eligible_volume_share",
    ):
        values = pd.to_numeric(coverage[column], errors="coerce")
        assert values.notna().all()
        assert np.isfinite(values.to_numpy()).all()
        assert (values >= 0.0).all()
    assert (coverage["eligible_stem_share"] <= 1.0 + 1e-12).all()
    assert (coverage["eligible_volume_share"] <= 1.0 + 1e-12).all()
    numeric_province = province.select_dtypes(include=[np.number])
    assert np.isfinite(numeric_province.to_numpy()).all()
    assert (numeric_province.to_numpy() >= 0.0).all()


@requires_artifacts
def test_mapping_local_donor_proxy_and_gaps(artifact) -> None:
    coverage = artifact["coverage"]
    gaps = artifact["gaps"]

    mapped = coverage[coverage["mapped"]]
    unmapped = coverage[~coverage["mapped"]]
    assert set(mapped["taper_status"]) == {"local_published", "donor_published"}
    assert set(unmapped["taper_status"]) == {"unmapped"}
    assert (mapped["taper_id"] != "UNMAPPED").all()
    assert (unmapped["taper_id"] == "UNMAPPED").all()
    assert (mapped["evidence_tier"] == "published_table").all()
    assert (unmapped["evidence_tier"] == "not_applicable").all()
    assert (mapped["local_vs_donor"] != "unmapped").all()
    assert (unmapped["local_vs_donor"] == "unmapped").all()
    assert (mapped["gap_reason"] == "not_applicable").all()
    assert (unmapped["gap_reason"] != "not_applicable").all()
    assert (unmapped["gap_reason"] != "").all()

    local = coverage[coverage["local_taper"]]
    donor = coverage[coverage["donor_taper"]]
    assert set(local["province"]).issubset({"BC", "QC"})
    assert (local["source_jurisdiction"] == local["province"]).all()
    assert (donor["source_jurisdiction"] != donor["province"]).all()
    assert set(donor["source_jurisdiction"]).issubset({"BC", "QC"})
    assert (local["taper_id"].str.contains("KOZAK2004|SCHNEIDER2013")).all()
    assert (donor["taper_id"].str.contains("DONOR")).all()

    assert len(gaps) == len(unmapped)
    assert set(gaps["species"]) == set(unmapped["species"])
    assert (gaps["gap_reason"] != "").all()
    assert (gaps["nearest_open_taper_jurisdiction"].isin(["BC", "QC"])).all()

    proxy = coverage[coverage["proxy_taper"]]
    assert (proxy["mapping_class"] == "proxy").all()
    exact = coverage[coverage["mapped"] & ~coverage["proxy_taper"]]
    assert (exact["mapping_class"] == "exact_species").all()
    assert (coverage.loc[~coverage["mapped"], "mapping_class"] == "unmapped").all()


@requires_artifacts
def test_priority_and_gap_ranking(artifact) -> None:
    province = artifact["province"]
    gaps = artifact["gaps"]
    summary = artifact["summary"]

    assert sorted(province["priority_rank"].tolist()) == list(range(1, 14))
    assert summary["priorities"][0]["priority_rank"] == 1
    assert (
        province.sort_values("priority_rank").iloc[0]["province"]
        == summary["priorities"][0]["province"]
    )
    assert len(summary["largest_gaps"]) <= 15
    assert len(summary["largest_gaps"]) == min(15, len(gaps))
    assert (
        gaps.sort_values("eligible_volume_pct", ascending=False)
        .iloc[0]["eligible_volume_pct"]
        >= gaps["eligible_volume_pct"].max() - 1e-12
    )
    for row in province.itertuples(index=False):
        assert row.priority_basis
        assert row.local_taper_opportunity_pct >= 0.0


@requires_artifacts
def test_provenance_hashes_and_labels(artifact) -> None:
    manifest = artifact["manifest"]
    summary = artifact["summary"]
    report = artifact["report"]
    coverage = artifact["coverage"]

    assert manifest["artifact"] == "national_taper_coverage_v1"
    assert manifest["unexpected_git_deletions"] == "none"
    assert manifest["labels"]["model_estimate"] is True
    assert manifest["labels"]["national_claim"] is False
    assert manifest["labels"]["restricted_data"] is False
    assert manifest["labels"]["request_dependency"] is False
    assert manifest["labels"]["ui_or_public_osecan_changed"] is False
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["national_claim"] is False
    assert summary["labels"]["restricted_data"] is False
    assert summary["labels"]["request_dependency"] is False
    assert summary["counts"]["jurisdictions"] == 13

    for record in manifest["outputs"]:
        path = DEFAULT_OUTPUT_DIR / record["path"]
        assert path.exists()
        assert path.stat().st_size == record["bytes"]
        assert _sha256(path) == record["sha256"]
    for record in manifest["inputs"].values():
        path = (DEFAULT_OUTPUT_DIR.parents[4] / record["path"]).resolve()
        assert path.exists()
        assert _sha256(path) == record["sha256"]
    for record in manifest["code"]:
        path = (DEFAULT_OUTPUT_DIR.parents[4] / record["path"]).resolve()
        assert path.exists()
        assert _sha256(path) == record["sha256"]

    assert coverage["model_estimate"].all()
    assert not coverage["national_claim"].any()
    assert not coverage["observed"].any()
    assert not coverage["policy_grade"].any()
    assert "not a national supply" in report
    assert "No request" in report
    assert "model_estimate" in report or "Model estimate" in report


@requires_artifacts
def test_pilot_overlay_is_labeled_not_substituted(artifact) -> None:
    coverage = artifact["coverage"]

    measured = coverage[coverage["pilot_measured_4province"]]
    assert set(measured["province"]).issubset({"BC", "QC", "NB", "YT"})
    assert measured["mapped"].all()
    assert coverage[coverage["pilot_v2_dispatch"]]["mapped"].all()
    assert coverage.groupby("province")["pilot_v2_dispatch"].any().all()
    # The pilot overlays are evidence pointers only; they never change the
    # register's model-estimate label or national-claim status.
    assert measured["model_estimate"].all()
    assert not measured["national_claim"].any()
