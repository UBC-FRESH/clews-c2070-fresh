"""Tests for the provincial open forest-inventory audit v1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "data/derived/national/inventory/provincial_open_inventory_audit_v1"
EXPECTED_PROVINCES = {"AB", "SK", "MB", "ON", "NS", "PE", "NL", "NT", "NU"}
CORE = {"species", "age", "height", "volume_biomass"}


def read_csv(name: str) -> list[dict[str, str]]:
    with (ARTIFACT / name).open(newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def sources() -> list[dict[str, str]]:
    return read_csv("source_table.csv")


@pytest.fixture(scope="module")
def stats() -> list[dict[str, str]]:
    return read_csv("sample_stats.csv")


def test_all_nine_e2_provinces_present(sources: list[dict[str, str]]) -> None:
    assert {row["province"] for row in sources} == EXPECTED_PROVINCES
    assert len({row["source_id"] for row in sources}) == len(sources)


def test_every_source_has_explicit_licence_or_gap(sources: list[dict[str, str]]) -> None:
    for row in sources:
        assert row["licence"].strip()
        if row["access_status"] != "gap":
            assert row["licence"].lower() != "not applicable"
            assert row["source_url"].startswith("http")
        if row["download_url"]:
            assert row["download_url"].startswith("http")


def test_no_request_dua_or_restricted_source(sources: list[dict[str, str]]) -> None:
    forbidden = ("request", "dua", "restricted data", "licensed_only", "private")
    for row in sources:
        haystack = " ".join([row["access_mode"], row["access_status"]]).lower()
        assert not any(token in haystack for token in forbidden), row["source_id"]
        assert "data use agreement" not in row["licence"].lower()


def test_spatial_level_and_attribute_flags_consistent(sources: list[dict[str, str]]) -> None:
    valid_levels = {"stand_polygon", "raster_cell_polygon", "none"}
    for row in sources:
        assert row["spatial_level"] in valid_levels
        for name in CORE:
            assert row[f"has_{name}"] in {"True", "False"}


def test_every_sample_file_exists_and_matches_row_count(
    sources: list[dict[str, str]],
) -> None:
    for row in sources:
        sample = row["sample_file"]
        if not sample:
            assert row["sampled"] == "False"
            continue
        path = ARTIFACT / sample
        assert path.is_file(), sample
        with path.open(newline="") as f:
            assert len(list(csv.DictReader(f))) == int(row["sample_rows"])


def test_manifest_hashes_match_samples() -> None:
    rows = read_csv("manifest.csv")
    assert rows
    for row in rows:
        path = ARTIFACT / row["sample_file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == row["sample_sha256"], row["source_id"]
        assert path.stat().st_size == int(row["sample_bytes"])


def test_full_artifacts_recorded_for_three_large_downloads() -> None:
    rows = read_csv("manifest.csv")
    full = {row["source_id"]: row for row in rows if row["full_artifact_sha256"]}
    assert {"ab_avi_crown", "mb_fri_1970s", "ns_forest_inventory"} <= set(full)
    for row in full.values():
        assert len(row["full_artifact_sha256"]) == 64
        assert int(row["full_artifact_bytes"]) > 0


def test_sample_stats_are_finite_non_negative_and_correct_n(
    stats: list[dict[str, str]],
) -> None:
    assert stats
    for row in stats:
        n = int(row["n"])
        assert 0 < n <= int(row["sample_rows"])
        values = [float(row[key]) for key in ("min", "p25", "median", "p75", "max", "mean")]
        assert all(not math.isnan(value) for value in values)
        assert min(values) >= 0
        assert float(row["min"]) <= float(row["p25"]) <= float(row["median"])
        assert float(row["median"]) <= float(row["p75"]) <= float(row["max"])


def test_height_and_volume_stats_exist_where_flags_claim_them(
    sources: list[dict[str, str]], stats: list[dict[str, str]]
) -> None:
    stats_by_source: dict[str, set[str]] = {}
    for row in stats:
        stats_by_source.setdefault(row["source_id"], set()).add(row["attribute_role"])
    for row in sources:
        if row["sampled"] != "True":
            continue
        roles = stats_by_source.get(row["source_id"], set())
        if row["has_height"] == "True":
            assert "height" in roles, row["source_id"]
        if row["has_volume_biomass"] == "True" and row["source_id"] != "sk_fri_forest_stands":
            assert "volume" in roles, row["source_id"]


def test_species_distribution_only_for_declared_species_sources(
    sources: list[dict[str, str]],
) -> None:
    rows = read_csv("species_distribution.csv")
    sampled = {row["source_id"] for row in sources if row["sampled"] == "True"}
    observed = {row["source_id"] for row in rows}
    assert observed <= sampled
    assert {"ab_avi_crown", "mb_fri_1970s", "ns_forest_inventory"} <= observed
    for row in rows:
        assert 0 < float(row["share"]) <= 1
        assert int(row["count"]) > 0


def test_gaps_cover_all_nine_provinces_and_are_actionable() -> None:
    rows = read_csv("gaps.csv")
    assert {row["province"] for row in rows} == EXPECTED_PROVINCES
    for row in rows:
        assert row["gap"].strip()
        assert row["detail"].strip()
        assert row["mitigation"].strip()
        assert row["gap_type"] in {"open_gap", "access_gap", "attribute_gap", "coverage_gap"}


def test_on_and_nu_are_not_claimed_as_sampled_attributes(sources: list[dict[str, str]]) -> None:
    by_province = {row["province"]: row for row in sources if row["province"] in {"ON", "NU"}}
    assert by_province["ON"]["sampled"] == "False"
    assert by_province["NU"]["sampled"] == "False"
    assert by_province["NU"]["access_status"] == "gap"


def test_no_synthetic_or_model_flags_in_source_table(sources: list[dict[str, str]]) -> None:
    for row in sources:
        text = json.dumps(row).lower()
        assert "synthetic" not in text or "no synthetic" in text or "does not produce" in text


def test_readme_states_no_ingest_and_no_request() -> None:
    text = " ".join((ARTIFACT / "README.md").read_text().lower().split())
    assert "does **not** ingest" in text
    assert "no request, dua, restricted data" in text
    assert "no value is synthetic" in text
