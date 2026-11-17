"""Tests for the national representative-stand batch."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from workflows.national.assortments.height import HeightModelRecord
from workflows.national.assortments.national_batch import (
    VALIDATION_PRODUCT_REL_TOL,
    VALIDATION_RESIDUE_REL_TOL,
    VALIDATION_TOTAL_REL_TOL,
    _volume_curve,
    aggregate_stand,
    build_buck_yield_table,
    interpolate_tree,
)
from workflows.national.assortments.synthesize import (
    DistributionSpec,
    SynthesisTarget,
    synthesize_tree_list,
)
from workflows.national.bucking.bucking import (
    buck_tree,
    load_grade_rules,
    load_price_profile,
)
from workflows.national.bucking.schema import TreeRecord
from workflows.national.bucking.taper import (
    TaperRegistry,
    default_kozak_parameter_path,
    default_nemora_root,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
RULES_PATH = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
PRICE_PATH = default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"


def _height_model(species: str = "BA", a: float = -0.4705, b: float = 1.0074) -> HeightModelRecord:
    return HeightModelRecord(
        scope="test",
        target_level="ecoprovince",
        target_id="test",
        fit_level="ecoprovince",
        fit_id="test:BA",
        species=species,
        ecodistrict=None,
        ecoregion=None,
        ecoprovince="test",
        ecozone="test",
        age_window=None,
        n=100,
        a=a,
        b=b,
        rmse_m=1.0,
        bias_m=0.1,
        evidence_tier="stratum_fit",
    )


def _tree(tree_id: str, dbh: float, height: float, expansion: float = 1.0) -> TreeRecord:
    return TreeRecord(
        tree_id=tree_id,
        site_id="test",
        species="BA",
        dbh_cm=dbh,
        height_m=height,
        status="live",
        expansion_factor=expansion,
        bec_zone="SBS",
        age=100.0,
        provenance="test",
    )


@pytest.fixture(scope="module")
def rules_and_prices():
    return load_grade_rules(RULES_PATH), load_price_profile(PRICE_PATH)


def test_buck_yield_table_matches_per_tree_sample(rules_and_prices):
    """The 2 cm table must reproduce a per-tree exact bucking sample."""
    rules, prices = rules_and_prices
    registry = TaperRegistry(parameter_path=default_kozak_parameter_path())
    model = registry.resolve("BC", "BA", "SBS")
    height = _height_model()
    table = build_buck_yield_table(
        taper_species="BA",
        taper_zone="SBS",
        height_a=height.a,
        height_b=height.b,
        height_fit_id="test:BA",
        density_code="BA",
        density_odt_m3=0.349843,
        density_source="test",
        density_evidence="assumption",
        rules=rules,
        prices=prices,
        registry=registry,
        bin_start_cm=2.0,
        bin_width_cm=2.0,
        bin_end_cm=42.0,
        table_key="test",
    )
    diameters = [8.3, 11.1, 14.7, 16.2, 19.9, 22.4, 25.1, 28.8, 31.3, 35.6, 39.1, 41.0]
    exact_products: dict[str, float] = {}
    exact_taper = 0.0
    exact_residue = 0.0
    estimate_products: dict[str, float] = {}
    estimate_taper = 0.0
    estimate_residue = 0.0
    for index, diameter in enumerate(diameters):
        tree = _tree(f"t{index}", diameter, float(height.predict(diameter)), expansion=1.0)
        logs, residue = buck_tree(
            tree, model, rules, prices, basic_density_odt_m3=0.349843
        )
        for log in logs:
            exact_products[log.product] = exact_products.get(log.product, 0.0) + log.volume_m3
        exact_taper += residue.taper_total_m3
        exact_residue += residue.residue_volume_m3
        estimate = interpolate_tree(table, diameter)
        for product, volume in estimate["products"].items():
            estimate_products[product] = estimate_products.get(product, 0.0) + volume
        estimate_taper += estimate["taper_total_m3"]
        estimate_residue += estimate["residue_m3"]

    assert abs(estimate_taper - exact_taper) / max(exact_taper, 1e-9) <= VALIDATION_TOTAL_REL_TOL
    assert abs(estimate_residue - exact_residue) / max(exact_residue, 1e-9) <= VALIDATION_RESIDUE_REL_TOL
    for product in set(exact_products).union(estimate_products):
        exact = exact_products.get(product, 0.0)
        estimate = estimate_products.get(product, 0.0)
        relative = abs(estimate - exact) / max(exact, 1e-9) if exact > 1e-3 else 0.0
        assert relative <= VALIDATION_PRODUCT_REL_TOL, (product, exact, estimate)


def test_interpolation_conserves_and_no_negative():
    table = {
        "table_key": "test",
        "density_odt_m3": 0.4,
        "bins": [
            {
                "bin_lower_cm": 2.0,
                "bin_upper_cm": 4.0,
                "bin_mid_cm": 3.0,
                "height_m": 5.0,
                "valid": True,
                "taper_total_m3": 0.02,
                "recovered_log_m3": 0.0,
                "residue_m3": 0.02,
                "residue_odt": 0.008,
                "products": {},
                "sorts": {},
                "n_logs": 0,
            },
            {
                "bin_lower_cm": 4.0,
                "bin_upper_cm": 6.0,
                "bin_mid_cm": 5.0,
                "height_m": 7.0,
                "valid": True,
                "taper_total_m3": 0.10,
                "recovered_log_m3": 0.08,
                "residue_m3": 0.02,
                "residue_odt": 0.008,
                "products": {"sawlog": 0.06, "pulpwood": 0.02},
                "sorts": {"sawlog|D125L8": 0.06, "pulpwood|D125L50": 0.02},
                "n_logs": 1,
            },
        ],
    }
    estimate = interpolate_tree(table, 4.0)
    assert estimate["taper_total_m3"] >= estimate["recovered_log_m3"]
    assert math.isclose(
        estimate["recovered_log_m3"] + estimate["residue_m3"],
        estimate["taper_total_m3"],
        rel_tol=1e-12,
    )
    assert all(value >= 0.0 for value in estimate["sorts"].values())
    assert math.isclose(
        sum(estimate["products"].values()),
        estimate["recovered_log_m3"],
        rel_tol=1e-12,
    )


def test_stand_volume_tph_and_assortment_conservation(rules_and_prices):
    rules, prices = rules_and_prices
    registry = TaperRegistry(parameter_path=default_kozak_parameter_path())
    height = _height_model()
    spec = DistributionSpec(
        species="BA",
        distribution="weibull",
        parameters={"a": 2.0, "beta": 12.0},
        support=(2.0, 60.0),
        fit_id="test:BA",
        evidence_tier="stratum_fit",
    )
    target = SynthesisTarget(
        target_id="test",
        target_level="ecoprovince",
        species_weights={"BA": 1.0},
        tph_total=500.0,
        volume_m3ha=200.0,
        area_ha=1.0,
        bec_zone="SBS",
        age=100.0,
        dbh_limit_cm=2.0,
        max_dbh_cm=60.0,
        n_trees=150,
        target_tph=500.0,
    )
    table = build_buck_yield_table(
        taper_species="BA",
        taper_zone="SBS",
        height_a=height.a,
        height_b=height.b,
        height_fit_id="test:BA",
        density_code="BA",
        density_odt_m3=0.349843,
        density_source="test",
        density_evidence="assumption",
        rules=rules,
        prices=prices,
        registry=registry,
        bin_start_cm=2.0,
        bin_width_cm=2.0,
        bin_end_cm=60.0,
        table_key="test",
    )
    stand = synthesize_tree_list(
        target,
        distributions=[spec],
        height_models=[height],
        taper_registry=registry,
    )
    assert math.isclose(stand.achieved_taper_volume_m3, 200.0, rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(stand.tph, 500.0, rel_tol=1e-9, abs_tol=1e-9)
    aggregation, validation = aggregate_stand(
        stand.trees,
        table,
        model=registry.resolve("BC", "BA", "SBS"),
        rules=rules,
        prices=prices,
        density_odt_m3=0.349843,
        sample_trees=10,
        seed=7,
    )
    assert math.isclose(
        aggregation["recovered_log_m3"] + aggregation["residue_m3"],
        aggregation["taper_total_m3"],
        rel_tol=1e-12,
    )
    assert all(value >= 0.0 for value in aggregation["sorts"].values())
    assert (
        abs(aggregation["taper_total_m3"] - stand.achieved_taper_volume_m3)
        / max(stand.achieved_taper_volume_m3, 1e-9)
        <= VALIDATION_TOTAL_REL_TOL
    )
    assert validation["status"] == "pass"


def test_species_product_totals_from_table():
    table = {
        "table_key": "test",
        "density_odt_m3": 0.4,
        "bins": [
            {
                "bin_lower_cm": 2.0,
                "bin_upper_cm": 4.0,
                "bin_mid_cm": 3.0,
                "height_m": 5.0,
                "valid": True,
                "taper_total_m3": 0.1,
                "recovered_log_m3": 0.08,
                "residue_m3": 0.02,
                "residue_odt": 0.008,
                "products": {"sawlog": 0.08},
                "sorts": {"sawlog|D125L8": 0.08},
                "n_logs": 1,
            },
            {
                "bin_lower_cm": 4.0,
                "bin_upper_cm": 6.0,
                "bin_mid_cm": 5.0,
                "height_m": 7.0,
                "valid": True,
                "taper_total_m3": 0.2,
                "recovered_log_m3": 0.15,
                "residue_m3": 0.05,
                "residue_odt": 0.02,
                "products": {"sawlog": 0.10, "pulpwood": 0.05},
                "sorts": {"sawlog|D125L8": 0.10, "pulpwood|D125L50": 0.05},
                "n_logs": 2,
            },
        ],
    }
    trees = [
        _tree("a", 3.0, float(_height_model().predict(3.0)), expansion=2.0),
        _tree("b", 5.0, float(_height_model().predict(5.0)), expansion=3.0),
    ]
    aggregation, _validation = aggregate_stand(
        trees,
        table,
        model=None,
        rules=None,
        prices=None,
        density_odt_m3=0.4,
        sample_trees=0,
    )
    assert math.isclose(aggregation["products"]["sawlog"], 2.0 * 0.08 + 3.0 * 0.10)
    assert math.isclose(aggregation["products"]["pulpwood"], 3.0 * 0.05)
    assert math.isclose(
        aggregation["taper_total_m3"],
        2.0 * 0.1 + 3.0 * 0.2,
        rel_tol=1e-12,
    )


def test_volume_curve_matches_exact_taper():
    registry = TaperRegistry(parameter_path=default_kozak_parameter_path())
    model = registry.resolve("BC", "BA", "SBS")
    curve = _volume_curve(model, -0.4705, 1.0074)
    for diameter in (2.0, 5.0, 10.0, 20.0, 40.0, 80.0, 150.0, 250.0):
        height = float(1.3 + math.exp(-0.4705) * diameter**1.0074)
        exact = model.total_volume_m3(diameter, height)
        interpolated = float(
            np.interp(
                diameter,
                np.asarray(curve["dbh_cm"]),
                np.asarray(curve["total_volume_m3"]),
            )
        )
        assert abs(interpolated - exact) / max(exact, 1e-9) <= 1e-3
