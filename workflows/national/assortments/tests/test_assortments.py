"""Synthetic contract tests for the national assortment prototype."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from nemora.core import MixtureComponentFit
from nemora.fit.mixture import sample_mixture

from workflows.national.assortments.assortments import (
    AssortmentError,
    build_assortment_matrix,
    conservation_summary,
)
from workflows.national.assortments.distributions import fit_dbh_distributions
from workflows.national.assortments.facility import (
    FacilityContract,
    FeedstockOffer,
    assign_feedstock,
)
from workflows.national.assortments.height import HeightModelRecord
from workflows.national.assortments.mixtures import (
    MixtureComponent,
    MixtureFitError,
    canonical_component_order,
    component_is_degenerate,
    fit_truncated_mixture,
)
from workflows.national.assortments.synthesize import (
    DistributionSpec,
    SynthesisError,
    SynthesisTarget,
    normalize_species_weights,
    synthesize_tree_list,
)

ECO = {
    "ecodistrict": "D1",
    "ecoregion": "R1",
    "ecoprovince": "P1",
    "ecozone": "Z1",
}


def _observations(
    *,
    species: str = "SW",
    count: int = 600,
    plots: int = 20,
    limit: float = 5.0,
    ecodistrict: str = "D1",
    ecoregion: str = "R1",
    seed: int = 1,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dbh = limit + rng.weibull(2.5, size=count) * 9.0
    return pd.DataFrame(
        {
            "species": species,
            "dbh_cm": dbh,
            "height_m": 1.3 + 0.45 * dbh,
            "expansion_factor": rng.uniform(1.0, 5.0, size=count),
            "plot_id": [f"P{index % plots:03d}" for index in range(count)],
            "age": rng.uniform(40.0, 120.0, size=count),
            "dbh_limit_cm": limit,
            "ecodistrict": ecodistrict,
            "ecoregion": ecoregion,
            "ecoprovince": "P1",
            "ecozone": "Z1",
        }
    )


def test_censored_fit_recovers_known_weibull() -> None:
    true_shape = 2.5
    true_scale = 9.0
    rng = np.random.default_rng(42)
    raw = true_scale * rng.weibull(true_shape, size=8000)
    raw = raw[raw >= 5.0]
    observations = pd.DataFrame(
        {
            "species": "SW",
            "dbh_cm": raw,
            "height_m": 1.3 + 0.45 * raw,
            "expansion_factor": np.ones(raw.size),
            "plot_id": [f"P{index % 40:03d}" for index in range(raw.size)],
            "age": 80.0,
            "dbh_limit_cm": 5.0,
            "ecodistrict": "D1",
            "ecoregion": "R1",
            "ecoprovince": "P1",
            "ecozone": "Z1",
        }
    )
    fits = fit_dbh_distributions(
        observations,
        scope="test",
        targets=[{"species": "SW", "ecodistrict": "D1"}],
        min_trees=30,
        min_plots=5,
        random_state=7,
    )
    assert len(fits) == 1
    fit = fits[0]
    assert fit.evidence_tier == "stratum_fit"
    assert fit.selected.distribution == "weibull"
    assert fit.selected.parameters["a"] == pytest.approx(true_shape, rel=0.15)
    assert fit.selected.parameters["beta"] == pytest.approx(true_scale, rel=0.20)
    assert fit.selected.ks < 0.08


def test_two_weibull_mixture_recovers_bimodal_components() -> None:
    rng = np.random.default_rng(0)
    low = 8.0 * rng.weibull(3.0, size=2600)
    high = 20.0 * rng.weibull(6.0, size=1400)
    dbh = np.concatenate([low, high])
    dbh = dbh[dbh >= 5.0]
    observations = pd.DataFrame(
        {
            "species": "SW",
            "dbh_cm": dbh,
            "height_m": 1.3 + 0.45 * dbh,
            "expansion_factor": rng.uniform(1.0, 4.0, size=dbh.size),
            "plot_id": [f"P{index % 40:03d}" for index in range(dbh.size)],
            "age": 80.0,
            "dbh_limit_cm": 5.0,
            "ecodistrict": "D1",
            "ecoregion": "R1",
            "ecoprovince": "P1",
            "ecozone": "Z1",
        }
    )
    fit = fit_dbh_distributions(
        observations,
        scope="test",
        targets=[{"species": "SW", "ecodistrict": "D1"}],
        min_trees=30,
        min_plots=5,
        random_state=11,
    )[0]
    by_name = {candidate.distribution: candidate for candidate in fit.candidates}
    mixture = by_name["mix_weibull_2"]
    assert mixture.is_success
    assert mixture.converged
    assert mixture.component_names == ("weibull", "weibull")
    weights = mixture.component_weights or ()
    assert sum(weights) == pytest.approx(1.0)
    assert all(weight >= 1e-4 for weight in weights)
    components = mixture.component_parameters or ()
    betas = [component["beta"] for component in components]
    assert betas == sorted(betas)
    assert components[0]["a"] == pytest.approx(3.0, rel=0.35)
    assert components[0]["beta"] == pytest.approx(8.0, rel=0.25)
    assert components[1]["a"] == pytest.approx(6.0, rel=0.35)
    assert components[1]["beta"] == pytest.approx(20.0, rel=0.25)
    assert weights[0] == pytest.approx(0.65, abs=0.15)
    assert weights[1] == pytest.approx(0.35, abs=0.15)
    assert mixture.aic < by_name["weibull"].aic - 20.0
    assert fit.selected.distribution in {"mix_weibull_2", "mix_weibull_gamma"}


def test_mixture_sampling_respects_dbh_limit() -> None:
    target = SynthesisTarget(
        target_id="mixture-support",
        target_level="au",
        species_weights={"SW": 1.0},
        tph_total=900.0,
        volume_m3ha=300.0,
        area_ha=1.0,
        bec_zone="SBS",
        age=100.0,
        dbh_limit_cm=5.0,
        max_dbh_cm=60.0,
        seed=13,
        n_trees=300,
    )
    spec = DistributionSpec(
        species="SW",
        distribution="mix_weibull_2",
        parameters={},
        support=(5.0, 60.0),
        component_weights=(0.6, 0.4),
        component_parameters=({"a": 2.5, "beta": 12.0}, {"a": 6.0, "beta": 24.0}),
        component_names=("weibull", "weibull"),
    )
    stand = synthesize_tree_list(
        target,
        distributions=[spec],
        height_models=[_height_model("SW")],
        taper_registry=_FakeRegistry(),
    )
    diameters = np.array([tree.dbh_cm for tree in stand.trees])
    assert diameters.size == target.n_trees
    assert np.all(diameters >= 5.0)
    assert np.all(diameters <= 60.0)
    assert stand.achieved_taper_volume_m3 == pytest.approx(target.target_volume_m3, rel=1e-9)


def test_component_ordering_carries_weights() -> None:
    high = MixtureComponent(name="weibull", weight=0.3, parameters={"a": 6.0, "beta": 20.0})
    low = MixtureComponent(name="weibull", weight=0.7, parameters={"a": 3.0, "beta": 8.0})
    ordered, swaps = canonical_component_order((high, low))
    assert swaps == 2
    assert [component.parameters["beta"] for component in ordered] == [8.0, 20.0]
    assert [component.weight for component in ordered] == [0.7, 0.3]


def test_degenerate_mixture_components_are_rejected() -> None:
    assert component_is_degenerate("weibull", 0.0, {"a": 2.0, "beta": 10.0})
    assert component_is_degenerate("weibull", 0.5, {"a": math.nan, "beta": 10.0})
    assert component_is_degenerate("gamma", 0.5, {"p": -1.0, "beta": 10.0})
    assert not component_is_degenerate("gamma", 0.5, {"p": 3.0, "beta": 10.0})

    rng = np.random.default_rng(4)
    dbh = np.concatenate(
        [8.0 * rng.weibull(3.0, size=500), 20.0 * rng.weibull(6.0, size=500)]
    )
    dbh = dbh[dbh >= 5.0]
    edges = np.arange(5.0, 45.0, 2.0)
    tallies, _ = np.histogram(dbh, bins=edges)
    mids = 0.5 * (edges[:-1] + edges[1:])
    with pytest.raises((MixtureFitError, ValueError)):
        fit_truncated_mixture(
            mids,
            tallies,
            ("weibull", "weibull"),
            support_lower=5.0,
            n_starts=1,
            min_weight=0.9,
        )


def test_mixed_weibull_gamma_fit_runs_and_samples() -> None:
    rng = np.random.default_rng(5)
    weibull = 10.0 * rng.weibull(2.5, size=1600)
    gamma = rng.gamma(shape=6.0, scale=3.0, size=900)
    dbh = np.concatenate([weibull, gamma])
    dbh = dbh[dbh >= 5.0]
    edges = np.arange(5.0, 55.0, 2.0)
    tallies, _ = np.histogram(dbh, bins=edges)
    mids = 0.5 * (edges[:-1] + edges[1:])
    fit = fit_truncated_mixture(
        mids,
        tallies,
        ("weibull", "gamma"),
        support_lower=5.0,
        n_starts=2,
        random_state=17,
    )
    assert fit.converged
    assert fit.n_valid_starts >= 1
    assert set(fit.component_names) == {"weibull", "gamma"}
    assert sum(fit.weights) == pytest.approx(1.0)
    assert all(weight >= 1e-4 for weight in fit.weights)
    assert all(component.parameters["beta"] > 0.0 for component in fit.components)

    components = [
        MixtureComponentFit(
            name=component.name,
            weight=component.weight,
            parameters=dict(component.parameters),
        )
        for component in fit.components
    ]
    samples = sample_mixture(2000, components, lower=5.0, random_state=19)
    assert samples.size == 2000
    assert np.all(np.isfinite(samples))
    assert np.all(samples >= 5.0)
def test_hierarchical_fallback_to_ecoregion() -> None:
    sparse = _observations(count=3, plots=1, ecodistrict="D1", ecoregion="R1")
    rich = _observations(count=300, plots=15, ecodistrict="D2", ecoregion="R1", seed=2)
    observations = pd.concat([sparse, rich], ignore_index=True)
    fits = fit_dbh_distributions(
        observations,
        scope="test",
        targets=[{"species": "SW", "ecodistrict": "D1"}],
        min_trees=30,
        min_plots=5,
        random_state=7,
    )
    fit = fits[0]
    assert fit.evidence_tier == "pooled_fit"
    assert fit.fit_level == "ecoregion"
    assert fit.n_trees >= 30
    assert fit.n_plots >= 5


class _FakeTaper:
    def __init__(self, species: str, bec_zone: str) -> None:
        self.species = species
        self.bec_zone = bec_zone

    def total_volume_m3(self, dbh_cm: float, height_m: float) -> float:
        return 0.00004 * float(dbh_cm) ** 2 * float(height_m)


class _FakeRegistry:
    def resolve(self, jurisdiction: str, species: str, bec_zone: str) -> _FakeTaper:
        return _FakeTaper(species, bec_zone)


def _height_model(species: str, *, a: float = 1.0, b: float = 0.55) -> HeightModelRecord:
    return HeightModelRecord(
        scope="test",
        target_level="ecodistrict",
        target_id=f"D1|{species}",
        fit_level="ecodistrict",
        fit_id=f"ecodistrict:D1|{species}",
        species=species,
        ecodistrict="D1",
        ecoregion="R1",
        ecoprovince="P1",
        ecozone="Z1",
        age_window=(60.0, 100.0),
        n=100,
        a=a,
        b=b,
        rmse_m=1.0,
        bias_m=0.0,
        evidence_tier="stratum_fit",
    )


def test_synthesized_tree_list_matches_target_volume() -> None:
    target = SynthesisTarget(
        target_id="test-stand",
        target_level="au",
        species_weights={"SW": 0.7, "PL": 0.3},
        tph_total=900.0,
        volume_m3ha=300.0,
        area_ha=1.0,
        bec_zone="SBS",
        age=100.0,
        dbh_limit_cm=5.0,
        max_dbh_cm=60.0,
        seed=11,
        n_trees=240,
    )
    distributions = [
        DistributionSpec(
            species="SW",
            distribution="weibull",
            parameters={"a": 2.2, "beta": 12.0},
            support=(5.0, 60.0),
            fit_id="test-SW",
            evidence_tier="stratum_fit",
        ),
        DistributionSpec(
            species="PL",
            distribution="gamma",
            parameters={"p": 4.0, "beta": 2.4},
            support=(5.0, 60.0),
            fit_id="test-PL",
            evidence_tier="stratum_fit",
        ),
    ]
    stand = synthesize_tree_list(
        target,
        distributions=distributions,
        height_models=[_height_model("SW"), _height_model("PL", a=0.9, b=0.6)],
        taper_registry=_FakeRegistry(),
    )
    assert stand.achieved_taper_volume_m3 == pytest.approx(target.target_volume_m3, rel=1e-9)
    assert stand.scale_factor > 0.0
    assert stand.tph > 0.0
    assert len(stand.trees) == target.n_trees
    assert all(tree.expansion_factor > 0.0 for tree in stand.trees)
    assert all(tree.height_m > 1.3 for tree in stand.trees)
    assert math.isclose(sum(target.species_weights.values()), 1.0, rel_tol=1e-12)


def test_species_weights_sum_and_minor_species_drop() -> None:
    weights = normalize_species_weights(
        {"SW": 0.5, "PL": 0.3, "AT": 0.15, "SB": 0.045, "AC": 0.005},
        min_share=0.01,
    )
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-12)
    assert "AC" not in weights
    assert weights["SW"] == pytest.approx(0.5 / 0.995)


def test_synthesis_rejects_unsupported_species() -> None:
    target = SynthesisTarget(
        target_id="test-stand",
        target_level="au",
        species_weights={"W": 1.0},
        tph_total=900.0,
        volume_m3ha=300.0,
        area_ha=1.0,
        bec_zone="SBS",
        age=100.0,
        dbh_limit_cm=5.0,
        max_dbh_cm=60.0,
        seed=11,
        n_trees=40,
    )

    class _UnsupportedRegistry:
        def resolve(self, jurisdiction: str, species: str, bec_zone: str):
            from workflows.national.bucking.taper import UnsupportedTaperError

            raise UnsupportedTaperError("not in the taper registry")

    with pytest.raises(SynthesisError):
        synthesize_tree_list(
            target,
            distributions=[
                DistributionSpec(
                    species="W",
                    distribution="weibull",
                    parameters={"a": 2.0, "beta": 10.0},
                    support=(5.0, 60.0),
                )
            ],
            height_models=[_height_model("W")],
            taper_registry=_UnsupportedRegistry(),
        )


def _assortment_frames():
    trees = pd.DataFrame(
        {
            "tree_id": ["T1", "T2"],
            "species": ["SW", "PL"],
            "expansion_factor": [10.0, 5.0],
        }
    )
    logs = pd.DataFrame(
        {
            "tree_id": ["T1", "T1", "T2"],
            "species": ["SW", "SW", "PL"],
            "length_m": [7.5, 5.0, 5.0],
            "large_end_dib_cm": [30.0, 24.0, 18.0],
            "small_end_dib_cm": [21.0, 15.0, 13.0],
            "volume_m3": [0.35, 0.15, 0.30],
            "sort_code": ["D20L8", "D125L50", "D125L50"],
            "product": ["sawlog", "pulpwood", "pulpwood"],
            "price_cad_m3": [80.0, 40.0, 40.0],
            "value_cad": [28.0, 6.0, 12.0],
            "evidence": ["published_table"] * 3,
            "grade_status": ["not_assigned"] * 3,
        }
    )
    residue = pd.DataFrame(
        {
            "tree_id": ["T1", "T2"],
            "species": ["SW", "PL"],
            "residue_volume_m3": [0.10, 0.05],
            "recovered_log_m3": [0.50, 0.30],
            "taper_total_m3": [0.60, 0.35],
        }
    )
    return logs, residue, trees


def test_assortment_volumes_and_residue_conserve_tree_volume() -> None:
    logs, residue, trees = _assortment_frames()
    matrix = build_assortment_matrix(logs, residue, trees)
    summary = conservation_summary(matrix)
    assert summary["pass"] is True
    assert summary["total_row_pass"] is True
    assert summary["recovered_log_m3"] == pytest.approx(0.50 * 10 + 0.30 * 5)
    assert summary["residue_m3"] == pytest.approx(0.10 * 10 + 0.05 * 5)
    assert summary["taper_total_m3"] == pytest.approx(0.60 * 10 + 0.35 * 5)
    assert summary["recovered_plus_residue_m3"] == pytest.approx(summary["taper_total_m3"])
    assert set(matrix["record_type"]) == {"log", "residue", "total"}
    assert (matrix["volume_m3"] >= 0.0).all()
    assert (matrix["n_logs"] >= 0).all()


def test_assortment_rejects_negative_volume() -> None:
    logs, residue, trees = _assortment_frames()
    logs.loc[0, "volume_m3"] = -1.0
    with pytest.raises(AssortmentError):
        build_assortment_matrix(logs, residue, trees)


def test_facility_interface_rejects_insufficient_capacity() -> None:
    offers = [
        FeedstockOffer(
            offer_id="O1",
            grade="SAW",
            product="sawlog",
            volume_m3=120.0,
            moisture_wet_basis=0.45,
            delivered_cost_cad_m3=65.0,
            origin="stand-1",
        )
    ]
    contracts = [
        FacilityContract(
            facility_id="F1",
            name="Sawmill",
            accepted_grades=("SAW",),
            min_volume_m3=50.0,
            max_volume_m3=100.0,
            min_contract_years=1,
            max_contract_years=5,
            gate_cost_cad_m3=5.0,
            destination="Prince George",
        )
    ]
    bundle = assign_feedstock(offers, contracts)
    assert not bundle.assignments
    assert len(bundle.unassigned) == 1
    assert "capacity" in bundle.unassigned[0].reason
    assert bundle.contract_totals[0]["assigned_volume_m3"] == 0.0
