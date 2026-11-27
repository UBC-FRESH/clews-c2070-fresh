"""Contract tests for the WS4-driven OSeCAN handoff."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = handoff.OUTPUT_DIR
REQUIRED = (
    "zone_process_residue.csv",
    "zone_harvest_residue.csv",
    "case_zone_summary.csv",
    "case_tier_positions.csv",
    "tier_comparison_vs_v10.csv",
    "summary.json",
    "manifest.json",
    "FORESTRY_OSECAN_HANDOFF_WS4.md",
)
requires_artifacts = pytest.mark.skipif(
    not all((OUTPUT / name).exists() for name in REQUIRED),
    reason="WS4 OSeCAN handoff has not been generated",
)

LABEL_COLUMNS = {
    "model_estimate": "true",
    "observed": "false",
    "policy_grade": "false",
    "national_claim": "false",
    "ws4_derived": "true",
    "official_osecan_changed": "false",
    "chain_v51_unchanged": "true",
}


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(OUTPUT / name)


def _summary() -> dict:
    return json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))


def _v10_tiers() -> pd.DataFrame:
    return pd.read_csv(handoff.V10_TIER_POSITIONS)


def _tier(pj: float, tiers: dict) -> str:
    for tier_id in ("T1", "T2", "T3", "T4"):
        if pj <= float(tiers[tier_id]["upper_pj"]) + 1e-9:
            return tier_id
    return "above_T4"


@pytest.fixture(scope="module")
def summary() -> dict:
    return _summary()


def test_zone_weights_sum_to_one_and_no_fabricated_zones(summary) -> None:
    weights = pd.read_csv(handoff.ZONE_WEIGHTS)
    assert set(weights["province"]) == set(handoff.PROVINCES)
    assert set(weights["zone_id"]) == set(handoff.ZONE_ORDER)
    assert weights["zone_id"].nunique() == 13
    grouped = weights.groupby("province")["weight"].sum()
    assert grouped.to_dict() == pytest.approx(
        {province: 1.0 for province in handoff.PROVINCES}, abs=1e-12
    )
    assert float(weights["weight"].sum()) == pytest.approx(13.0, abs=1e-12)
    for province in handoff.MULTI_ZONE_PROVINCES:
        assert weights.loc[weights["province"] == province, "zone_id"].nunique() == 2
    for province in set(handoff.PROVINCES) - set(handoff.MULTI_ZONE_PROVINCES):
        assert weights.loc[weights["province"] == province, "zone_id"].nunique() == 1

    crosswalk = pd.read_csv(handoff.OSECAN_CROSSWALK)
    official_names = dict(zip(crosswalk["zone_id"], crosswalk["zone_name"]))
    assert set(official_names) == set(handoff.ZONE_ORDER)
    for row in weights.itertuples(index=False):
        assert official_names[row.zone_id] == row.zone_name
    assert summary["zone_weights"]["zone_count"] == 13
    assert summary["zone_weights"]["province_count"] == 13
    assert summary["zone_weights"]["global_weight_sum"] == pytest.approx(
        13.0, abs=1e-12
    )

    for name in ("zone_process_residue.csv", "zone_harvest_residue.csv"):
        frame = _read(name)
        assert frame["zone_id"].nunique() == 13
        assert set(frame["zone_id"]) == set(handoff.ZONE_ORDER)
        assert "UNMAPPED" not in set(frame["zone_id"])
        assert len(frame) == len(handoff.BASES) * 13 * (
            4 if "process" in name else 3
        )


def test_zone_conservation_and_case_arithmetic(summary) -> None:
    product_chain = pd.read_csv(handoff.WS4_PRODUCT_CHAIN)
    weights = pd.read_csv(handoff.ZONE_WEIGHTS).set_index(["province", "zone_id"])
    process = _read("zone_process_residue.csv")
    harvest = _read("zone_harvest_residue.csv")

    for basis in handoff.BASES:
        provinces = product_chain[
            (product_chain["basis"] == basis)
            & (product_chain["geography_level"] == "province")
        ].set_index("province")
        assert set(provinces.index) == set(handoff.PROVINCES)
        process_base = float(provinces["process_residue_pj"].sum())

        selected = process[process["basis"] == basis]
        for case, fraction in handoff.PROCESS_CASES:
            rows = selected[selected["availability_case"] == case]
            assert len(rows) == 13
            assert float(rows["pj"].sum()) == pytest.approx(
                process_base * fraction, rel=1e-12, abs=1e-9
            )
            assert float(rows["national_case_pj"].iloc[0]) == pytest.approx(
                process_base * fraction, rel=1e-12, abs=1e-9
            )
            assert not rows["pj"].isna().any()
            assert not (rows[["pj", "residue_odt"]] < 0).any().any()
            for row in rows.itertuples(index=False):
                assert row.pj == pytest.approx(
                    row.residue_odt * handoff.LHV_GJ_PER_ODT / 1e6,
                    rel=1e-12,
                    abs=1e-9,
                )
                assert row.share_of_national_case == pytest.approx(
                    row.pj / row.national_case_pj
                    if row.national_case_pj
                    else 0.0,
                    rel=1e-12,
                    abs=1e-12,
                )

        selected = harvest[harvest["basis"] == basis]
        for case, fraction in handoff.HARVEST_CASES:
            odt_column, pj_column = handoff.HARVEST_COLUMNS[case]
            expected_pj = float(provinces[pj_column].sum())
            expected_odt = float(provinces[odt_column].sum())
            rows = selected[selected["availability_case"] == case]
            assert len(rows) == 13
            assert float(rows["pj"].sum()) == pytest.approx(
                expected_pj, rel=1e-12, abs=1e-9
            )
            assert float(rows["residue_odt"].sum()) == pytest.approx(
                expected_odt, rel=1e-12, abs=1e-6
            )
            assert not rows["pj"].isna().any()
            assert not (rows[["pj", "residue_odt"]] < 0).any().any()
            for row in rows.itertuples(index=False):
                assert row.pj == pytest.approx(
                    row.residue_odt * handoff.LHV_GJ_PER_ODT / 1e6,
                    rel=1e-12,
                    abs=1e-9,
                )

        # Reconstruct each zone from the frozen weights and the origin-province
        # rows; this is the like-for-like zone-conservation check.
        for stream, cases in (
            ("process", handoff.PROCESS_CASES),
            ("harvest", handoff.HARVEST_CASES),
        ):
            for case, fraction in cases:
                expected = {zone_id: 0.0 for zone_id in handoff.ZONE_ORDER}
                for province in handoff.PROVINCES:
                    if stream == "process":
                        province_pj = (
                            float(provinces.loc[province, "process_residue_pj"])
                            * fraction
                        )
                    else:
                        province_pj = float(
                            provinces.loc[province, handoff.HARVEST_COLUMNS[case][1]]
                        )
                    for zone_id in handoff.ZONE_ORDER:
                        key = (province, zone_id)
                        if key in weights.index:
                            expected[zone_id] += province_pj * float(
                                weights.loc[key, "weight"]
                            )
                frame = process if stream == "process" else harvest
                rows = frame[
                    (frame["basis"] == basis)
                    & (frame["availability_case"] == case)
                ].set_index("zone_id")
                for zone_id in handoff.ZONE_ORDER:
                    assert float(rows.loc[zone_id, "pj"]) == pytest.approx(
                        expected[zone_id], rel=1e-12, abs=1e-9
                    )

    assert summary["zone_conservation"]["zone_rows"] == 182
    assert summary["zone_conservation"]["zone_rows_expected"] == 182
    assert summary["zone_conservation"]["max_zone_national_gap_pj"] <= 1e-6
    assert summary["zone_conservation"]["max_province_zone_gap_pj"] <= 1e-6


def test_tier_positions_match_frozen_v5_1_and_v10(summary) -> None:
    tiers = handoff._load_tiers()
    v10 = _v10_tiers()
    assert len(v10) == 21
    keys = set(
        zip(v10["scenario"], v10["stream"], v10["case"], strict=True)
    )
    assert keys == set(handoff.FROZEN_V51_TIERS)
    for row in v10.itertuples(index=False):
        key = (row.scenario, row.stream, row.case)
        assert handoff.FROZEN_V51_TIERS[key] == row.tier
        assert _tier(float(row.national_pj), tiers) == row.tier
    assert summary["tier_comparison"]["v10_positions_matching_frozen_v5_1"] == 21
    assert summary["tier_comparison"]["frozen_v5_1_positions"] == 21
    assert summary["tier_comparison"]["ws4_positions_available"] == 7
    assert summary["tier_comparison"]["ws4_primary_tier_matches"] == 7
    assert summary["tier_comparison"]["ws4_secondary_tier_matches"] == 7

    positions = _read("case_tier_positions.csv")
    assert len(positions) == 14
    assert set(positions["basis"]) == set(handoff.BASES)
    v10_119 = v10[v10["scenario"] == "119m"].set_index(
        ["stream", "case"]
    )
    for row in positions.itertuples(index=False):
        reference = v10_119.loc[(row.stream, row.case)]
        assert row.tier == reference["tier"]
        assert row.v10_119_tier == reference["tier"]
        assert bool(row.tier_changed) is False
        assert float(row.national_pj) == pytest.approx(
            float(row.v10_119_national_pj) + float(row.ws4_minus_v10_pj),
            rel=1e-12,
            abs=1e-9,
        )
        assert float(row.ws4_abs_delta_pj) == pytest.approx(
            abs(float(row.ws4_minus_v10_pj)), rel=1e-12, abs=1e-15
        )

    comparison = _read("tier_comparison_vs_v10.csv")
    assert len(comparison) == 21
    assert comparison.groupby("scenario").size().to_dict() == {
        "100m": 7,
        "119m": 7,
        "150m": 7,
    }
    for row in comparison.itertuples(index=False):
        assert bool(row.v10_tier_matches_frozen) is True
        if row.scenario == "119m":
            assert row.comparison_status == (
                "compared_national_totals_origin_province"
            )
            assert not math.isnan(float(row.ws4_national_pj_primary))
            assert not math.isnan(float(row.ws4_national_pj_secondary))
            assert row.ws4_tier_primary == row.v10_tier
            assert row.ws4_tier_secondary == row.v10_tier
            assert str(row.ws4_tier_changed_primary).lower() == "false"
            assert str(row.ws4_tier_changed_secondary).lower() == "false"
        else:
            assert row.comparison_status == "not_available_ws4_chain_119m_only"
            assert row.ws4_national_pj_primary == "not_available"
            assert row.ws4_national_pj_secondary == "not_available"
            assert row.ws4_tier_primary == "not_available"
            assert row.ws4_tier_secondary == "not_available"
            assert row.ws4_tier_changed_primary == "not_available"
            assert row.ws4_tier_changed_secondary == "not_available"


def test_official_osecan_hash_and_frozen_inputs(summary) -> None:
    hashes, digest = handoff._hash_tree(handoff.OSECAN_DIR)
    assert len(hashes) == 146
    assert digest == handoff.EXPECTED_OSECAN_TREE_SHA256
    integrity = summary["osecan_integrity"]
    assert integrity["file_count_before"] == 146
    assert integrity["file_count_after"] == 146
    assert integrity["tree_sha256_before"] == handoff.EXPECTED_OSECAN_TREE_SHA256
    assert integrity["tree_sha256_after"] == handoff.EXPECTED_OSECAN_TREE_SHA256
    assert integrity["unchanged"] is True
    assert integrity["changed_files"] == []
    assert not (OUTPUT / "candidate_activity_limits.csv").exists()
    assert OUTPUT != handoff.OSECAN_DIR
    assert handoff.OSECAN_DIR not in OUTPUT.parents

    frozen = summary["frozen_inputs_integrity"]
    assert frozen["unchanged"] is True
    assert frozen["before"] == frozen["after"]
    for name in ("handoff_v10", "curve_v9", "ws4_chain"):
        assert frozen["before"][name]["file_count"] > 0


def test_labels_origin_basis_and_no_nan_negative(summary) -> None:
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "ws4_derived": True,
        "official_osecan_changed": False,
        "chain_v51_unchanged": True,
        "ui_changed": False,
        "transport_cost_in_osecan": False,
        "facility_province_handoff": False,
    }
    assert summary["basis"]["origin_province"] is True
    assert summary["basis"]["facility_province_handoff"] is False

    for name in (
        "zone_process_residue.csv",
        "zone_harvest_residue.csv",
        "case_zone_summary.csv",
        "case_tier_positions.csv",
        "tier_comparison_vs_v10.csv",
    ):
        frame = _read(name)
        for column, expected in LABEL_COLUMNS.items():
            assert set(frame[column].astype(str).str.lower()) == {expected}, (
                name,
                column,
            )
        if "province_basis" in frame.columns:
            assert set(frame["province_basis"]) == {"origin_province"}
        numeric = frame.select_dtypes(include=["number"])
        assert not numeric.isna().any().any(), name
        residue_columns = [
            column
            for column in numeric.columns
            if column
            not in {
                "ws4_minus_v10_pj",
                "ws4_minus_v10_pj_primary",
                "ws4_minus_v10_pj_secondary",
            }
        ]
        assert not (numeric[residue_columns] < 0).any().any(), name

    zone_rows = pd.concat(
        [_read("zone_process_residue.csv"), _read("zone_harvest_residue.csv")]
    )
    assert zone_rows["province_basis"].eq("origin_province").all()
    assert zone_rows["caveat"].str.contains("origin-province").all()
    assert zone_rows["share_of_national_case"].between(0.0, 1.0).all()


def test_manifest_and_no_unexpected_deletions(summary) -> None:
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "pass"
    assert manifest["validation"]["failed_check_count"] == 0
    assert manifest["osecan_integrity"]["unchanged"] is True
    for name, entry in manifest["outputs"].items():
        path = OUTPUT / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    assert summary["validation"]["passed"] is True
    assert summary["d_status"]["no_unexpected_deletions"] is True

    for repository in (REPO_ROOT, REPO_ROOT / "data"):
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            assert not (len(line) >= 2 and (line[0] == "D" or line[1] == "D")), (
                repository,
                line,
            )
