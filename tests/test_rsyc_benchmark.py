"""Focused tests for RSYC benchmark curve evaluation, matching, and metrics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from workflows.national.rsyc_benchmark.benchmark import (
    MGM_NATIVE_MAX_AGE,
    RsycLookup,
    _composite_prediction,
    _mgm_mai_endpoint,
    _mgm_target_rows,
    age_bin_label,
    area_weighted_mean,
    canonical_species_code,
    derive_qc_mix,
    evaluate_rsyc_curve,
    leading_species,
    parse_bec_zone,
    parse_mgm_ygrp,
    regression_metrics,
    resolve_bc_geography,
    summarise_pairs,
)


def test_evaluate_rsyc_curve_matches_published_equation() -> None:
    b1, b2, b3, b4 = 198.340196, 0.020671, 0.533751, 0.000756
    ages = np.array([1.0, 50.0, 100.0, 150.0])
    expected = b1 * np.exp(-b4 * ages) * (1.0 - np.exp(-b2 * ages)) ** b3
    actual = evaluate_rsyc_curve(ages, b1, b2, b3, b4)
    np.testing.assert_allclose(actual, expected, rtol=1e-12)


def test_evaluate_rsyc_curve_accepts_scalar_and_rejects_invalid_ages() -> None:
    values = evaluate_rsyc_curve(100.0, 200.0, 0.02, 1.0, 0.001)
    assert values.shape == (1,)
    assert values[0] > 0

    for invalid_age in (0.0, -1.0, 151.0, math.nan):
        with pytest.raises(ValueError):
            evaluate_rsyc_curve(np.array([invalid_age]), 200.0, 0.02, 1.0, 0.001)


def test_regression_metrics_known_values() -> None:
    metrics = regression_metrics([100.0, 200.0, 300.0], [110.0, 220.0, 330.0])
    assert metrics["n"] == 3
    assert metrics["bias"] == pytest.approx(20.0)
    assert metrics["mae"] == pytest.approx(20.0)
    assert metrics["rmse"] == pytest.approx(math.sqrt(1400.0 / 3.0))
    assert metrics["relative_bias"] == pytest.approx(20.0 / 200.0)
    assert metrics["correlation"] == pytest.approx(1.0)
    assert metrics["within_20pct"] == pytest.approx(1.0)


def test_regression_metrics_empty_and_constant_prediction() -> None:
    empty = regression_metrics([], [])
    assert empty["n"] == 0
    assert math.isnan(empty["bias"])

    constant = regression_metrics([100.0, 200.0], [150.0, 150.0])
    assert math.isnan(constant["correlation"])


def test_age_bin_label_is_inclusive() -> None:
    assert age_bin_label(40) == "40-60"
    assert age_bin_label(60) == "40-60"
    assert age_bin_label(61) is None
    assert age_bin_label(100) == "80-100"
    assert age_bin_label(140) == "120-140"


def test_canonical_species_code_handles_bc_and_canonical_forms() -> None:
    mapping = {"HW": "TSUG.HET", "FD": "PSEU.MEN"}
    assert canonical_species_code("HW", mapping) == "TSUG.HET"
    assert canonical_species_code("TSUG.HET.NA", mapping) == "TSUG.HET"
    assert canonical_species_code("PSEU.MEN", mapping) == "PSEU.MEN"
    assert canonical_species_code("ZZ", mapping) is None
    assert canonical_species_code(None, mapping) is None


def test_parse_mgm_ygrp() -> None:
    parsed = parse_mgm_ygrp("DC-Sb_8_A_80_20")
    assert parsed == {
        "prefix": "DC",
        "species_code": "Sb",
        "height": 8,
        "density": "A",
        "age": 80,
        "crown_closure": 20,
    }
    assert parse_mgm_ygrp("not-a-ygrp") is None


def test_lookup_resolution_order_and_error() -> None:
    models = pd.DataFrame(
        {
            "model_id": ["species_model", "group_model", "generic_model"],
            "strata_id": ["Ecozone/Unit"] * 3,
            "species": ["PICE.GLA", "coniferous", "generic"],
            "model_group": ["species", "coniferous", "generic"],
            "b1": [100.0, 90.0, 80.0],
            "b2": [0.02, 0.02, 0.02],
            "b3": [1.0, 1.0, 1.0],
            "b4": [0.0, 0.0, 0.0],
        }
    )
    lookup = RsycLookup(models, {})

    curve, level = lookup.curve_for("Ecozone/Unit", "PICE.GLA")
    assert curve.model_id == "species_model"
    assert level == "species"

    curve, level = lookup.curve_for("Ecozone/Unit", "PINU.BAN")
    assert curve.model_id == "group_model"
    assert level == "coniferous_group"

    lookup_no_group = RsycLookup(models.loc[models["model_group"] != "coniferous"], {})
    curve, level = lookup_no_group.curve_for("Ecozone/Unit", "PINU.BAN")
    assert curve.model_id == "generic_model"
    assert level == "generic_group"

    with pytest.raises(KeyError):
        lookup.curve_for("Missing/Unit", "PICE.GLA")


def test_lookup_prefers_national_fit_cell_model() -> None:
    models = pd.DataFrame(
        {
            "model_id": ["cell_model", "species_model"],
            "strata_id": ["Ecozone/Unit", "Ecozone/Unit"],
            "species": ["PICE.GLA", "PICE.GLA"],
            "model_group": ["species", "species"],
            "b1": [111.0, 222.0],
            "b2": [0.02, 0.02],
            "b3": [1.0, 1.0],
            "b4": [0.0, 0.0],
        }
    )
    lookup = RsycLookup(models, {("Ecozone/Unit", "PICE.GLA"): "cell_model"})
    curve, level = lookup.curve_for("Ecozone/Unit", "PICE.GLA")
    assert curve.model_id == "cell_model"
    assert level == "national_fit_cell"


def test_leading_species_reports_mapped_ba_share() -> None:
    leader, share = leading_species(
        {"HW": 60.0, "FD": 30.0, "UNKNOWN": 10.0},
        {"HW": "TSUG.HET", "FD": "PSEU.MEN"},
    )
    assert leader == "TSUG.HET"
    assert share == pytest.approx(0.9)


def test_area_weighted_mean_rejects_empty_weights() -> None:
    assert area_weighted_mean([1.0, 3.0], [10.0, 20.0]) == pytest.approx(17.5)
    with pytest.raises(ValueError):
        area_weighted_mean([0.0, 0.0], [10.0, 20.0])


def test_summarise_pairs_supports_global_rows() -> None:
    frame = pd.DataFrame(
        {
            "group": ["a", "a", "b"],
            "observed_volume": [100.0, 200.0, 300.0],
            "predicted_volume": [110.0, 180.0, 330.0],
        }
    )
    grouped = summarise_pairs(frame, ["group"])
    assert len(grouped) == 2
    assert grouped.loc[grouped["group"] == "a", "n"].item() == 2
    global_row = summarise_pairs(frame, [])
    assert global_row["n"].item() == 3


def test_parse_bec_zone_handles_two_and_three_letter_zones() -> None:
    assert parse_bec_zone("CWHvm1") == ("CWH", "vm")
    assert parse_bec_zone("CHxw") == ("CH", "xw")
    assert parse_bec_zone("MHmm1") == ("MH", "mm")
    assert parse_bec_zone("SBPSmc") == ("SBPS", "mc")
    assert parse_bec_zone("CMAunp") == (None, None)


def test_bc_fallback_visit_and_partial_ba_composite() -> None:
    geography = resolve_bc_geography(
        "Haida Gwaii",
        "MHmm1",
        {},
        {},
        {"Pacific Maritime/Northern Coastal Mountains"},
    )
    assert geography is not None
    assert geography.mapping_source == "bec_zone_fallback"
    assert geography.ecoprovince == "Northern Coastal Mountains"
    assert geography.ecoprovince_stratum == (
        "Pacific Maritime/Northern Coastal Mountains"
    )
    assert geography.bec_zone == "MH"

    models = pd.DataFrame(
        {
            "model_id": ["tsug", "pseu"],
            "strata_id": ["Pacific Maritime/Northern Coastal Mountains"] * 2,
            "species": ["TSUG.HET", "PSEU.MEN"],
            "model_group": ["species", "species"],
            "b1": [100.0, 200.0],
            "b2": [0.02, 0.02],
            "b3": [1.0, 1.0],
            "b4": [0.0, 0.0],
        }
    )
    lookup = RsycLookup(models, {})
    composite = _composite_prediction(
        lookup,
        "Pacific Maritime/Northern Coastal Mountains",
        {"HW": 60.0, "FD": 30.0, "UNKNOWN": 10.0},
        100.0,
    )
    assert composite.ba_total_m2ha == pytest.approx(100.0)
    assert composite.ba_code_mapped_m2ha == pytest.approx(90.0)
    assert composite.ba_curve_resolved_m2ha == pytest.approx(90.0)
    assert composite.ba_code_unmapped_m2ha == pytest.approx(10.0)
    growth = 1.0 - math.exp(-0.02 * 100.0)
    expected_total = (60.0 * 100.0 * growth + 30.0 * 200.0 * growth) / 100.0
    expected_curve = (60.0 * 100.0 * growth + 30.0 * 200.0 * growth) / 90.0
    assert composite.predicted_total_ba == pytest.approx(expected_total)
    assert composite.predicted_curve_ba == pytest.approx(expected_curve)


def test_qc_mix_zero_total_is_separate_and_invalid_mix_rejected() -> None:
    zero = derive_qc_mix(0.0, 0.0, 0.0)
    assert zero.status == "zero_total"
    assert zero.conifer_share is None
    assert zero.deciduous_share is None

    balanced = derive_qc_mix(10.0, 5.0, 15.0)
    assert balanced.status == "positive_total"
    assert balanced.conifer_share == pytest.approx(2.0 / 3.0)
    assert balanced.deciduous_share == pytest.approx(1.0 / 3.0)

    mismatch = derive_qc_mix(10.0, 5.0, 20.0)
    assert mismatch.status == "mass_balance_mismatch"
    assert mismatch.conifer_share is None

    negative = derive_qc_mix(-1.0, 5.0, 4.0)
    assert negative.status == "invalid_components"

    non_finite = derive_qc_mix(math.nan, 5.0, 5.0)
    assert non_finite.status == "invalid_components"


def test_regression_metrics_publishes_positive_hit_rate_denominator() -> None:
    metrics = regression_metrics([0.0, 100.0, 200.0], [10.0, 110.0, 220.0])
    assert metrics["n"] == 3
    assert metrics["n_positive_observed"] == 2
    assert metrics["n_zero_observed"] == 1
    assert metrics["within_20pct"] == pytest.approx(1.0)


def test_mgm_target_rows_keep_fixed_group_across_supported_ages() -> None:
    frame = pd.DataFrame(
        {
            "ygrp": ["DC-Sb_8_A_80_20"] * 3,
            "Age": [80, 90, 100],
            "age": [80, 80, 80],
        }
    )
    target = _mgm_target_rows(frame, ages=(50, 100, 150))
    assert target["Age"].tolist() == [100]
    assert target["starting_age"].item() == 80
    assert target["age_offset"].item() == 20


def test_mgm_mai_endpoint_is_censored_at_native_maximum() -> None:
    frame = pd.DataFrame(
        {
            "c_a": [1.0],
            "c_b": [2.0],
            "c_c": [0.0],
            "d_a": [0.0],
            "d_b": [1.0],
            "d_c": [0.0],
        }
    )
    ages, status = _mgm_mai_endpoint(frame)
    assert ages[0] == MGM_NATIVE_MAX_AGE
    assert status[0] == "right_censored_at_native_max"
