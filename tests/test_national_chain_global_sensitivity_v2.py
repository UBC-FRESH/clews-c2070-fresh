"""Contract tests for the v4 E1+E2c Plackett-Burman sensitivity."""

from __future__ import annotations

import hashlib
import itertools
import json
import math

import pandas as pd
import pytest

from workflows.national.facilities import (
    facility_residue_supply_v7_trade as trade_supply,
)
from workflows.national.facilities import (
    national_chain_global_sensitivity_v2 as sensitivity,
)

REQUIRED = (
    "design_matrix.csv",
    "responses.csv",
    "main_effects.csv",
    "overall_ranking.csv",
    "level_sweep.csv",
    "alias_structure.csv",
    "factor_levels.csv",
    "bf_component_by_province.csv",
    "v1_v2_comparison.csv",
    "v1_v2_response_comparison.csv",
    "summary.json",
    "manifest.json",
    "NATIONAL_CHAIN_GLOBAL_SENSITIVITY_V2.md",
)
requires_artifacts = pytest.mark.skipif(
    not all((sensitivity.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="national chain global sensitivity v2 has not been generated",
)
requires_trade_supply = pytest.mark.skipif(
    not (trade_supply.OUTPUT_DIR / "summary.json").exists(),
    reason="facility residue supply v7 trade has not been generated",
)

FACTOR_KEYS = tuple(factor.key for factor in sensitivity.FACTORS)
RESPONSE_KEYS = tuple(response.key for response in sensitivity.RESPONSES)
PHYSICAL_RESPONSE_KEYS = tuple(
    key for key in RESPONSE_KEYS if key != "national_margin_m_cad"
)


def _load_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(sensitivity.OUTPUT_DIR / name)


def _load_json(name: str) -> dict:
    return json.loads(
        (sensitivity.OUTPUT_DIR / name).read_text(encoding="utf-8")
    )


@requires_artifacts
def test_design_orthogonality_and_alias_structure() -> None:
    design = _load_csv("design_matrix.csv")
    assert len(design) == 16
    codes = design[[f"{key}_code" for key in FACTOR_KEYS]]
    assert set(codes.to_numpy().ravel()) == {-1, 1}
    for key in FACTOR_KEYS:
        assert codes[f"{key}_code"].value_counts().to_dict() == {
            -1: 8,
            1: 8,
        }
    matrix = codes.to_numpy(dtype=int)
    for left, right in itertools.combinations(range(len(FACTOR_KEYS)), 2):
        assert int(matrix[:, left] @ matrix[:, right]) == 0

    aliases = _load_csv("alias_structure.csv")
    assert len(aliases) == 7
    assert aliases["members"].str.count(r"\*").gt(0).all()
    assert (aliases["aliased_with"].str.startswith("dummy_column_")).all()
    covered = sum(
        len(members.split(" = ")) for members in aliases["members"]
    )
    assert covered == len(FACTOR_KEYS) * (len(FACTOR_KEYS) - 1) // 2


@requires_artifacts
def test_response_arithmetic() -> None:
    responses = _load_csv("responses.csv")
    assert len(responses) == 16
    for _, row in responses.iterrows():
        assert row["national_process_residue_pj"] == pytest.approx(
            row["process_base_pj"] * row["process_availability"],
            rel=1e-10,
            abs=1e-8,
        )
        assert row["harvest_recoverable_pj"] == pytest.approx(
            row["harvest_gross_pj"] * row["harvest_recovery"],
            rel=1e-10,
            abs=1e-8,
        )
        assert row["total_gross_pj"] == pytest.approx(
            row["national_process_residue_pj"] + row["harvest_gross_pj"],
            rel=1e-10,
            abs=1e-8,
        )
        assert row["bc_process_pj"] == pytest.approx(
            row["bc_process_base_pj"] * row["process_availability"],
            rel=1e-10,
            abs=1e-8,
        )
        assert row["bc_b2_process_pj"] == pytest.approx(
            row["bc_process_pj"] * row["b2_weight"],
            rel=1e-10,
            abs=1e-8,
        )
        assert row["national_total_recoverable_pj"] == pytest.approx(
            row["national_process_residue_pj"]
            + row["harvest_recoverable_pj"],
            rel=1e-10,
            abs=1e-8,
        )
        assert 0.0 <= row["harvest_recovery"] <= 1.0
        assert 0.0 <= row["process_availability"] <= 1.0
        assert 0.0 <= row["b2_weight"] <= 1.0


@requires_artifacts
def test_v4_source_values_and_residue_basis() -> None:
    responses = _load_csv("responses.csv")
    for key in PHYSICAL_RESPONSE_KEYS:
        values = responses[key]
        assert values.notna().all()
        assert (values >= 0.0).all()
    assert responses["national_margin_m_cad"].notna().all()
    assert responses["national_margin_m_cad"].isin(
        [-655.319124, 5205.328748]
    ).all()

    local_total = responses[
        (responses["product_coefficients"] == "e2c")
        & (responses["residue_basis"] == "stem_plus_branch_foliage")
        & (responses["capacity_assignment"] == "local_v6")
    ].iloc[0]
    assert local_total["process_base_pj"] == pytest.approx(
        498.7509169204482, rel=1e-10
    )
    assert local_total["harvest_gross_pj"] == pytest.approx(
        444.41076607635307, rel=1e-10
    )
    assert local_total["bc_process_base_pj"] == pytest.approx(
        181.40944828539065, rel=1e-10
    )

    local_stem = responses[
        (responses["product_coefficients"] == "e2c")
        & (responses["residue_basis"] == "stem_only")
        & (responses["capacity_assignment"] == "local_v6")
    ].iloc[0]
    assert local_stem["harvest_gross_pj"] == pytest.approx(
        127.83191034137693, rel=1e-10
    )
    assert local_stem["bf_increment_pj"] == pytest.approx(0.0, abs=1e-9)

    donor_stem = responses[
        (responses["product_coefficients"] == "donor_v1")
        & (responses["residue_basis"] == "stem_only")
    ].iloc[0]
    assert donor_stem["harvest_gross_pj"] == pytest.approx(
        167.77851362533755, rel=1e-10
    )
    donor_total = responses[
        (responses["product_coefficients"] == "donor_v1")
        & (responses["residue_basis"] == "stem_plus_branch_foliage")
    ]
    assert len(donor_total) == 4
    assert (
        donor_total["residue_basis_complete"].astype(str).str.lower()
        == "false"
    ).all()
    assert (donor_total["bf_increment_pj"] > 0.0).all()
    assert donor_total["harvest_gross_pj"].iloc[0] == pytest.approx(
        353.5585621785024, rel=1e-10
    )

    trade_total = responses[
        (responses["product_coefficients"] == "e2c")
        & (responses["residue_basis"] == "stem_plus_branch_foliage")
        & (responses["capacity_assignment"] == "trade_v5")
    ].iloc[0]
    assert trade_total["bc_process_base_pj"] == pytest.approx(
        253.214460183, rel=1e-10
    )
    assert trade_total["bc_process_base_pj"] > local_total[
        "bc_process_base_pj"
    ]


@requires_artifacts
def test_ranking_and_material_effects() -> None:
    effects = _load_csv("main_effects.csv")
    overall = _load_csv("overall_ranking.csv").set_index("factor_key")
    assert len(effects) == len(RESPONSE_KEYS) * len(FACTOR_KEYS)
    assert sorted(overall["rank"].tolist()) == list(
        range(1, len(FACTOR_KEYS) + 1)
    )
    for response_key in RESPONSE_KEYS:
        rows = effects[effects["response_key"] == response_key]
        assert sorted(rows["rank_for_response"].tolist()) == list(
            range(1, len(FACTOR_KEYS) + 1)
        )
        assert rows["effect_high_minus_low"].notna().all()
        assert rows["normalized_abs_effect"].between(0.0, 1.0).all()
        assert rows["material_effect"].dtype == bool

    def rank(response_key: str, factor_key: str) -> int:
        row = effects[
            (effects["response_key"] == response_key)
            & (effects["factor_key"] == factor_key)
        ]
        return int(row.iloc[0]["rank_for_response"])

    assert rank("national_process_residue_pj", "process_availability") == 1
    assert rank("harvest_gross_pj", "residue_basis") == 1
    assert rank("total_gross_pj", "process_availability") == 1
    assert rank("national_margin_m_cad", "price_scenario") == 1
    assert rank("bc_process_pj", "process_availability") == 1
    assert rank("bc_process_pj", "capacity_assignment") == 2
    assert {
        rank("harvest_recoverable_pj", "residue_basis"),
        rank("harvest_recoverable_pj", "harvest_recovery"),
    } == {1, 2}
    assert overall.loc["zone_weights", "responses_with_material_effect"] == 0
    assert overall.loc["price_scenario", "responses_with_material_effect"] == 2
    assert overall.loc["process_availability", "rank"] == 1
    assert overall.loc["residue_basis", "rank"] == 2
    assert overall.loc["product_coefficients", "rank"] == 5
    assert overall.loc["capacity_assignment", "rank"] == 6


@requires_artifacts
def test_v1_comparison_and_three_factor_caveat() -> None:
    comparison = _load_csv("v1_v2_comparison.csv")
    assert len(comparison) == len(FACTOR_KEYS)
    assert sorted(comparison["v1_rank"].tolist()) == list(
        range(1, len(FACTOR_KEYS) + 1)
    )
    assert sorted(comparison["v2_rank"].tolist()) == list(
        range(1, len(FACTOR_KEYS) + 1)
    )
    assert comparison["v1_rank"].tolist() == comparison["v2_rank"].tolist()
    coefficient_row = comparison[
        comparison["factor_key"] == "product_coefficients"
    ].iloc[0]
    assert "E1 four-province" in coefficient_row["comparison_note"]

    response_comparison = _load_csv("v1_v2_response_comparison.csv")
    assert len(response_comparison) == len(RESPONSE_KEYS) * len(FACTOR_KEYS)

    summary = _load_json("summary.json")
    caveat_text = " ".join(summary["caveats"])
    assert "Three-factor interactions" in caveat_text
    assert "not E2 v1" in caveat_text or "not E2 v1" in " ".join(
        str(value) for value in summary["caveats"]
    )


@requires_artifacts
def test_level_sweep_and_labels() -> None:
    sweep = _load_csv("level_sweep.csv")
    for factor in sensitivity.FACTORS:
        levels = sweep.loc[
            sweep["varied_factor"] == factor.key, "varied_level"
        ].tolist()
        for documented in factor.documented_levels:
            assert any(
                (
                    float(level) == float(documented)
                    if isinstance(documented, (int, float))
                    else str(level) == str(documented)
                )
                for level in levels
            )
    baseline = sweep[
        sweep["is_baseline_level"].astype(str).str.lower() == "true"
    ]
    assert not baseline.empty
    for _, row in sweep.iterrows():
        assert row["evidence_label"] == "model_estimate"
        assert str(row["observed"]).lower() == "false"
        assert str(row["policy_grade"]).lower() == "false"
        assert str(row["national_claim"]).lower() == "false"
        assert str(row["screening_only"]).lower() == "true"
        assert str(row["official_osecan_changed"]).lower() == "false"
        assert str(row["not_policy"]).lower() == "true"

    responses = _load_csv("responses.csv")
    for column in (
        "evidence_label",
        "observed",
        "policy_grade",
        "national_claim",
        "screening_only",
        "official_osecan_changed",
        "not_policy",
    ):
        assert responses[column].notna().all()
    assert (responses["evidence_label"] == "model_estimate").all()
    assert (responses["observed"].astype(str).str.lower() == "false").all()
    assert (
        responses["policy_grade"].astype(str).str.lower() == "false"
    ).all()
    assert (
        responses["national_claim"].astype(str).str.lower() == "false"
    ).all()


@requires_artifacts
def test_summary_manifest_and_output_hashes() -> None:
    summary = _load_json("summary.json")
    manifest = _load_json("manifest.json")
    assert summary["artifact"] == sensitivity.ARTIFACT
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["national_claim"] is False
    assert summary["labels"]["screening_only"] is True
    assert summary["design"]["runs"] == 16
    assert summary["design"]["factors"] == 7
    assert manifest["artifact"] == sensitivity.ARTIFACT
    assert manifest["labels"] == summary["labels"]
    for name, info in manifest["outputs"].items():
        path = sensitivity.OUTPUT_DIR / name
        assert path.is_file()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == info["sha256"]
        assert path.stat().st_size == info["bytes"]


@requires_artifacts
def test_no_nan_or_negative_physical_values() -> None:
    responses = _load_csv("responses.csv")
    numeric = responses.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    for key in PHYSICAL_RESPONSE_KEYS:
        assert not (responses[key] < 0.0).any()
    assert not responses["national_margin_m_cad"].isna().any()
    assert all(
        math.isfinite(value) for value in responses["national_margin_m_cad"]
    )


@requires_trade_supply
def test_trade_supply_artifact() -> None:
    summary = json.loads(
        (trade_supply.OUTPUT_DIR / "summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["artifact"] == trade_supply.ARTIFACT
    assert summary["status"] == "pass"
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["product_chain"]["artifact"] == "national_product_chain_v5"
    assert summary["trade_sensitivity"]["enabled"] is True

    local = json.loads(
        (
            trade_supply.DATA_ROOT
            / "derived/national/facilities/facility_residue_supply_v7"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    local_national = local["national_totals"]
    trade_national = summary["national_totals"]
    assert trade_national["process_residue_pj"] == pytest.approx(
        local_national["process_residue_pj"], rel=1e-12
    )
    assert trade_national["harvest_residue_gross_pj"] == pytest.approx(
        local_national["harvest_residue_gross_pj"], rel=1e-12
    )

    rows = pd.read_csv(
        trade_supply.OUTPUT_DIR / "province_residue_supply.csv"
    )
    bc_process = rows[
        (rows["province"] == "BC")
        & (rows["residue_type"] == "process_residue")
    ].iloc[0]
    local_rows = pd.read_csv(
        trade_supply.DATA_ROOT
        / "derived/national/facilities/facility_residue_supply_v7"
        / "province_residue_supply.csv"
    )
    local_bc = local_rows[
        (local_rows["province"] == "BC")
        & (local_rows["residue_type"] == "process_residue")
    ].iloc[0]
    assert bc_process["residue_pj"] > local_bc["residue_pj"]
    assert bc_process["residue_pj"] == pytest.approx(
        253.214460183, rel=1e-9
    )
