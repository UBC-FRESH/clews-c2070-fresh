"""Tests for the RSYC national volume-curve backbone ladder and area join."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from workflows.national.assortments.rsyc_backbone import (
    RsycBackboneError,
    build_area_targets,
    build_model_index,
    canonical_strata_id,
    evaluate_volume_curve,
    evidence_tier_for,
    fallback_depth_for,
    select_curve,
)

HIERARCHY = {
    "ecodistrict": "Z/P/R/D",
    "ecoregion": "Z/P/R",
    "ecoprovince": "Z/P",
    "ecozone": "Z",
    "tile": "T1",
}


def _model(model_id: str, level: str, key: str, species: str) -> dict:
    return {
        "model_id": model_id,
        "strata_level": level,
        "strata_id": key,
        "species": species,
        "b1": 100.0,
        "b2": 0.05,
        "b3": 2.0,
        "b4": 0.01,
    }


def _index(*models: dict) -> dict[tuple[str, str, str], dict]:
    return {
        (model["strata_level"], model["strata_id"], model["species"]): model
        for model in models
    }


def test_exact_ecodistrict_species_is_direct() -> None:
    index = _index(
        _model("ecodistrict-pice-mar", "ecodistrict", "Z/P/R/D", "PICE.MAR"),
        _model("ecoregion-pice-mar", "ecoregion", "Z/P/R", "PICE.MAR"),
    )
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.status == "ok"
    assert selection.model_id == "ecodistrict-pice-mar"
    assert selection.selected_level == "ecodistrict"
    assert selection.fallback_depth == 0
    assert selection.evidence_tier == "rsyc_direct"


def test_species_falls_back_to_ecoprovince() -> None:
    index = _index(
        _model("ecoprovince-pice-gla", "ecoprovince", "Z/P", "PICE.GLA"),
    )
    selection = select_curve(
        target_species="PICE.GLA",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.selected_level == "ecoprovince"
    assert selection.fallback_depth == 2
    assert selection.evidence_tier == "rsyc_fallback"


def test_tile_species_beats_ecodistrict_group() -> None:
    index = _index(
        _model("ecodistrict-coniferous", "ecodistrict", "Z/P/R/D", "coniferous"),
        _model("tile-pice-mar", "tile", "T1", "PICE.MAR"),
    )
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.model_id == "tile-pice-mar"
    assert selection.selected_level == "tile"
    assert selection.fallback_depth == 4
    assert selection.evidence_tier == "rsyc_fallback"


def test_group_is_used_only_after_every_species_level() -> None:
    index = _index(
        _model("ecodistrict-coniferous", "ecodistrict", "Z/P/R/D", "coniferous"),
    )
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.selected_level == "ecodistrict"
    assert selection.fallback_depth == 5
    assert selection.evidence_tier == "rsyc_group"
    assert selection.source_species == "coniferous"


def test_generic_is_the_last_resort() -> None:
    index = _index(_model("tile-generic", "tile", "T1", "generic"))
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.selected_level == "tile"
    assert selection.fallback_depth == 14
    assert selection.evidence_tier == "rsyc_group"
    assert selection.source_species == "generic"


def test_group_target_uses_broadleaf_group_without_species_search() -> None:
    index = _index(
        _model("ecodistrict-broadleaf", "ecodistrict", "Z/P/R/D", "broadleaf"),
        _model("ecodistrict-coniferous", "ecodistrict", "Z/P/R/D", "coniferous"),
    )
    selection = select_curve(
        target_species="BROADLEAF",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.source_species == "broadleaf"
    assert selection.fallback_depth == 5
    assert selection.evidence_tier == "rsyc_group"


def test_missing_curve_returns_explicit_failure() -> None:
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index={},
    )
    assert selection.status == "no_curve"
    assert selection.model_id is None
    assert selection.selected_level is None
    assert selection.fallback_depth == -1
    assert selection.evidence_tier == "none"
    assert selection.failure_reason


def test_ecoprovince_target_is_direct_at_ecoprovince() -> None:
    index = _index(_model("ecoprovince-pice-mar", "ecoprovince", "Z/P", "PICE.MAR"))
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecoprovince",
        hierarchy_keys=HIERARCHY,
        model_index=index,
    )
    assert selection.selected_level == "ecoprovince"
    assert selection.fallback_depth == 0
    assert selection.evidence_tier == "rsyc_direct"


def test_tile_first_selection_is_never_direct() -> None:
    index = _index(_model("tile-pice-mar", "tile", "T1", "PICE.MAR"))
    selection = select_curve(
        target_species="PICE.MAR",
        target_level="ecodistrict",
        hierarchy_keys=HIERARCHY,
        model_index=index,
        search_levels=("tile",),
        evidence_target_level="ecodistrict",
    )
    assert selection.selected_level == "tile"
    assert selection.fallback_depth == 4
    assert selection.evidence_tier == "rsyc_fallback"


def test_evidence_tier_classification() -> None:
    assert (
        evidence_tier_for(
            selected_level="ecodistrict",
            target_level="ecodistrict",
            is_group=False,
        )
        == "rsyc_direct"
    )
    assert (
        evidence_tier_for(
            selected_level="ecozone",
            target_level="ecodistrict",
            is_group=False,
        )
        == "rsyc_fallback"
    )
    assert (
        evidence_tier_for(
            selected_level="ecodistrict",
            target_level="ecodistrict",
            is_group=True,
        )
        == "rsyc_group"
    )
    assert (
        fallback_depth_for(
            selected_level="tile", target_level="ecodistrict", kind="group"
        )
        == 9
    )


def test_curve_evaluation_matches_published_equation() -> None:
    b1, b2, b3, b4, age = 120.0, 0.04, 2.5, 0.005, 100.0
    expected = b1 * math.exp(-b4 * age) * (1.0 - math.exp(-b2 * age)) ** b3
    value = evaluate_volume_curve(b1=b1, b2=b2, b3=b3, b4=b4, age=age, model_id="test")
    assert value == pytest.approx(expected)


def test_curve_evaluation_accepts_negative_b4_but_rejects_invalid_inputs() -> None:
    assert evaluate_volume_curve(b1=100.0, b2=0.05, b3=2.0, b4=-0.002, age=100.0) > 0.0
    with pytest.raises(RsycBackboneError):
        evaluate_volume_curve(b1=100.0, b2=0.05, b3=2.0, b4=0.01, age=0.0)
    with pytest.raises(RsycBackboneError):
        evaluate_volume_curve(b1=100.0, b2=0.05, b3=2.0, b4=0.01, age=151.0)
    with pytest.raises(RsycBackboneError):
        evaluate_volume_curve(b1=0.0, b2=0.05, b3=2.0, b4=0.01, age=50.0)
    with pytest.raises(RsycBackboneError):
        evaluate_volume_curve(b1=100.0, b2=float("nan"), b3=2.0, b4=0.01, age=50.0)


def test_ecodistrict_438_spelling_correction_is_explicit() -> None:
    source = (
        "Boreal Shield East/Eastern Boreal Shield/Central Laurentians/"
        "Betsiamites Plateau"
    )
    corrected = canonical_strata_id(source)
    assert corrected.endswith("Betsiamiles Plateau")
    assert canonical_strata_id("Z/P/R/D") == "Z/P/R/D"


def _strata() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "join_key": "Z/P/R1/D1",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecoregion": "R1",
                "ecodistrict": "D1",
                "ecodistrict_id": 1,
            },
            {
                "join_key": "Z/P/R2/D2",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecoregion": "R2",
                "ecodistrict": "D2",
                "ecodistrict_id": 2,
            },
        ]
    )


def test_area_targets_join_positive_area_and_ecoprovince_residual() -> None:
    ecodistrict_weights = pd.DataFrame(
        [
            {
                "unit_id": "P:1:AA",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecodistrict": "D1",
                "province": "AA",
                "rsyc_species": "PICE.MAR",
                "area_ha": 100.0,
            },
            {
                "unit_id": "P:1:AA",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecodistrict": "D1",
                "province": "AA",
                "rsyc_species": "PICE.MAR",
                "area_ha": 50.0,
            },
            {
                "unit_id": "P:1:AA",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecodistrict": "D1",
                "province": "AA",
                "rsyc_species": "BROADLEAF",
                "area_ha": 0.0,
            },
            {
                "unit_id": "P:2:AA",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecodistrict": "D2",
                "province": "AA",
                "rsyc_species": "CONIFEROUS",
                "area_ha": 25.0,
            },
            {
                "unit_id": "P:9:AA",
                "ecozone": "Z",
                "ecoprovince": "P",
                "ecodistrict": "Missing",
                "province": "AA",
                "rsyc_species": "PICE.MAR",
                "area_ha": 10.0,
            },
        ]
    )
    ecoprovince_weights = pd.DataFrame(
        [
            {
                "unit_id": "P",
                "ecozone": "Z",
                "ecoprovince": "P",
                "rsyc_species": "PICE.MAR",
                "area_ha": 150.0,
            },
            {
                "unit_id": "P",
                "ecozone": "Z",
                "ecoprovince": "P",
                "rsyc_species": "CONIFEROUS",
                "area_ha": 25.0,
            },
            {
                "unit_id": "P",
                "ecozone": "Z",
                "ecoprovince": "P",
                "rsyc_species": "PINU.BAN",
                "area_ha": 20.0,
            },
        ]
    )
    targets, diagnostics = build_area_targets(
        ecodistrict_weights=ecodistrict_weights,
        ecoprovince_weights=ecoprovince_weights,
        strata={"ecodistrict": _strata()},
    )

    assert diagnostics["zero_area_rows"] == 1
    assert diagnostics["zero_area_ha"] == 0.0
    assert diagnostics["unmapped_rows"] == 1
    assert diagnostics["unmapped_area_ha"] == 10.0
    assert diagnostics["duplicate_unit_species_rows"] == 1
    assert diagnostics["ecoprovince_residual_rows"] == 1
    assert diagnostics["ecoprovince_residual_area_ha"] == 20.0
    assert diagnostics["target_area_ha"] == pytest.approx(195.0)

    assert len(targets) == 3
    assert targets["area_ha"].gt(0).all()
    assert targets["cell_key"].is_unique

    direct = targets.loc[
        targets["unit_id"].eq("P:1:AA") & targets["target_species"].eq("PICE.MAR")
    ].iloc[0]
    assert direct["area_ha"] == pytest.approx(150.0)
    assert direct["ecodistrict_key"] == "Z/P/R1/D1"
    assert direct["province"] == "AA"

    residual = targets.loc[targets["target_level"].eq("ecoprovince")].iloc[0]
    assert residual["target_species"] == "PINU.BAN"
    assert residual["area_ha"] == pytest.approx(20.0)
    assert residual["ecodistrict_key"] is None


def test_model_index_rejects_non_positive_coefficients() -> None:
    frame = pd.DataFrame(
        [
            {
                "model_id": "bad",
                "strata_level": "tile",
                "strata_id": "T1",
                "species": "PICE.MAR",
                "b1": 0.0,
                "b2": 0.05,
                "b3": 2.0,
                "b4": 0.01,
            }
        ]
    )
    with pytest.raises(RsycBackboneError):
        build_model_index(frame)
