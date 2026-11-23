"""Tests for four-province branch/foliage allometry and recovery scenarios."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.bc_biomass_allometry import (
    load_bc_biomass_allometry,
)
from workflows.national.bucking.four_province_biomass_allometry import (
    DEFAULT_RECOVERY_FACTORS,
    FOUR_PROVINCE_MAP_PATH,
    REFERENCE_RECOVERY_FACTOR,
    compute_measured_components,
    load_four_province_biomass_allometry,
)
from workflows.national.bucking.four_province_bucking_pilot import (
    DEFAULT_OUTPUT_DIR,
    PILOT_PROVINCES,
)
from workflows.national.bucking.pilot_schema import SchemaError

REQUIRED = (
    "tree_list.csv",
    "residue_ledger.csv",
    "province_summary.csv",
    "coefficient_comparison.csv",
    "allometry_coverage.csv",
    "residue_recovery_scenarios.csv",
    "summary.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="four-province allometry artifacts have not been generated",
)


@pytest.fixture(scope="module")
def lookup():
    return load_four_province_biomass_allometry()


@pytest.fixture(scope="module")
def artifact() -> dict[str, object]:
    return {
        "trees": pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_list.csv"),
        "residue": pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv"),
        "province": pd.read_csv(DEFAULT_OUTPUT_DIR / "province_summary.csv"),
        "comparison": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "coefficient_comparison.csv"
        ),
        "coverage": pd.read_csv(DEFAULT_OUTPUT_DIR / "allometry_coverage.csv"),
        "scenarios": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "residue_recovery_scenarios.csv"
        ),
    }


def test_map_covers_four_provinces_and_all_species(lookup) -> None:
    assert {key[0] for key in lookup.by_nfi} == set(PILOT_PROVINCES)
    assert FOUR_PROVINCE_MAP_PATH.is_file()
    # Every exact NFI/species row in the committed measured list resolves.
    trees = pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_list.csv")
    for row in trees.itertuples(index=False):
        equations = lookup.resolve(
            row.jurisdiction, row.species, row.species_nfi
        )
        assert equations["branch"].component == "branch"
        assert equations["foliage"].component == "foliage"


def test_bc_map_matches_legacy_bc_allometry(lookup) -> None:
    legacy = load_bc_biomass_allometry()
    for code, equations in legacy.items():
        if code.startswith("__"):
            continue
        for component in ("branch", "foliage"):
            four = lookup.by_nfi[("BC", code)][component]
            old = equations[component]
            assert four.a == pytest.approx(old.a)
            assert four.b == pytest.approx(old.b)
            assert four.c == pytest.approx(old.c)
            assert four.reference == old.reference


def test_equation_values_recompute(lookup) -> None:
    qc = compute_measured_components(
        tree_id="qc",
        jurisdiction="QC",
        species="BOP",
        species_nfi="BETU.PAP",
        species_group="deciduous",
        dbh_cm=24.0,
        height_m=18.0,
        lookup=lookup,
    )
    branch = lookup.by_nfi[("QC", "BETU.PAP")]["branch"]
    expected = branch.a * 24.0**branch.b * 18.0**branch.c / 1000.0
    assert qc.branch_odt == pytest.approx(expected, rel=1e-12)
    assert qc.branch_foliage_odt == pytest.approx(
        qc.branch_odt + qc.foliage_odt, rel=1e-12
    )


def test_proxy_labels_are_explicit(lookup) -> None:
    hm = compute_measured_components(
        tree_id="hm",
        jurisdiction="BC",
        species="HM",
        species_nfi="HM",
        species_group="conifer",
        dbh_cm=12.8,
        height_m=6.9,
        lookup=lookup,
    )
    assert hm.proxy is True
    assert hm.species_specific is False
    assert hm.equation_species == "Conifers"

    birch = compute_measured_components(
        tree_id="bop",
        jurisdiction="NB",
        species="BOP",
        species_nfi="BETU.ALL",
        species_group="deciduous",
        dbh_cm=30.0,
        height_m=20.0,
        lookup=lookup,
    )
    assert birch.proxy is True
    assert birch.species_specific is False
    assert "Betula.ALL" in birch.branch_equation.proxy_reason

    exact = compute_measured_components(
        tree_id="bop2",
        jurisdiction="NB",
        species="BOP",
        species_nfi="BETU.PAP",
        species_group="deciduous",
        dbh_cm=30.0,
        height_m=20.0,
        lookup=lookup,
    )
    assert exact.proxy is False
    assert exact.species_specific is True


def test_unmapped_or_out_of_domain_fails_closed(lookup) -> None:
    with pytest.raises(SchemaError, match="no CFS branch/foliage equation"):
        compute_measured_components(
            tree_id="bad",
            jurisdiction="QC",
            species="XYZ",
            species_nfi="XYZ.ALL",
            species_group="conifer",
            dbh_cm=20.0,
            height_m=15.0,
            lookup=lookup,
        )
    with pytest.raises(SchemaError, match="outside the documented allometry domain"):
        compute_measured_components(
            tree_id="bad2",
            jurisdiction="YT",
            species="SW",
            species_nfi="PICE.GLA",
            species_group="conifer",
            dbh_cm=150.0,
            height_m=20.0,
            lookup=lookup,
        )


@requires_artifacts
def test_per_tree_conservation_and_scenarios(artifact, lookup) -> None:
    residue = artifact["residue"]
    trees = artifact["trees"]
    assert len(residue) == len(trees) == 591
    assert residue["branch_foliage_estimated"].all()
    assert (residue["residue_component_scope"] == "stem_plus_branch_foliage").all()
    assert residue["branch_foliage_source_licence"].eq(
        "Open Government Licence - Canada"
    ).all()
    for row in residue.itertuples(index=False):
        assert row.branch_foliage_odt == pytest.approx(
            row.branch_odt + row.foliage_odt, rel=1e-12, abs=1e-12
        )
        assert row.total_residue_odt == pytest.approx(
            row.stem_residue_odt + row.branch_foliage_odt,
            rel=1e-12,
            abs=1e-12,
        )
        assert row.total_residue_odt_expanded == pytest.approx(
            row.total_residue_odt * row.expansion_factor,
            rel=1e-12,
            abs=1e-12,
        )
        for factor in DEFAULT_RECOVERY_FACTORS:
            suffix = f"r{round(factor * 100):03d}"
            assert getattr(row, f"recoverable_residue_odt_{suffix}") == pytest.approx(
                row.total_residue_odt * factor, rel=1e-12, abs=1e-12
            )
            assert getattr(
                row, f"recoverable_residue_odt_{suffix}_expanded"
            ) == pytest.approx(
                row.total_residue_odt * factor * row.expansion_factor,
                rel=1e-12,
                abs=1e-12,
            )
    numeric = residue.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()


@requires_artifacts
def test_province_recovery_scenarios(artifact) -> None:
    scenarios = artifact["scenarios"]
    province = artifact["province"]
    assert set(scenarios["province"]) == set(PILOT_PROVINCES)
    for row in scenarios.itertuples(index=False):
        assert row.recovery_factor_reference == REFERENCE_RECOVERY_FACTOR
        assert row.recovery_factor_calibrated is False or str(
            row.recovery_factor_calibrated
        ).lower() == "false"
        assert row.total_residue_odt_expanded == pytest.approx(
            row.stem_residue_odt_expanded + row.branch_foliage_odt_expanded,
            rel=1e-9,
            abs=1e-6,
        )
        for factor in DEFAULT_RECOVERY_FACTORS:
            suffix = f"r{round(factor * 100):03d}"
            value = getattr(row, f"recoverable_residue_odt_{suffix}_expanded")
            assert value == pytest.approx(
                row.total_residue_odt_expanded * factor, rel=1e-9, abs=1e-6
            )
            rate = getattr(
                row, f"recoverable_residue_odt_per_recovered_m3_{suffix}"
            )
            assert rate == pytest.approx(
                value / row.recovered_gross_m3_expanded, rel=1e-9, abs=1e-12
            )
    merge = scenarios.merge(
        province[["province", "total_residue_odt_expanded"]],
        on="province",
        suffixes=("", "_summary"),
    )
    assert len(merge) == len(scenarios)
    assert np.allclose(
        merge["total_residue_odt_expanded"].astype(float),
        merge["total_residue_odt_expanded_summary"].astype(float),
        rtol=1e-9,
        atol=1e-6,
    )


@requires_artifacts
def test_coefficient_comparison_carries_total_scenarios(artifact) -> None:
    comparison = artifact["comparison"]
    for row in comparison.itertuples(index=False):
        assert row.model_total_residue_gross_odt_per_recovered_m3 == pytest.approx(
            row.model_stem_residue_odt_per_recovered_m3
            + row.model_branch_foliage_odt_per_recovered_m3,
            rel=1e-9,
            abs=1e-12,
        )
        for factor, suffix in ((0.35, "r035"), (0.60, "r060"), (0.75, "r075")):
            assert getattr(
                row, f"model_total_residue_recoverable_odt_per_recovered_m3_{suffix}"
            ) == pytest.approx(
                row.model_total_residue_gross_odt_per_recovered_m3 * factor,
                rel=1e-9,
                abs=1e-12,
            )
        assert row.model_recovery_factor_calibrated is False or str(
            row.model_recovery_factor_calibrated
        ).lower() == "false"
        assert math.isfinite(row.total_residue_odt_model_over_facility)
