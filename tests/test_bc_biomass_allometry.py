"""Tests for BC branch/foliage allometry in national bucking pilot v2.

The tests use the committed v2 artifact for ledger identities and recompute the
allometry from the committed measured tree list for reproducibility.
"""

from __future__ import annotations

import hashlib
import json
import math

import pandas as pd
import pytest

from workflows.national.bucking.bc_biomass_allometry import (
    ALLOMETRY_MAP_PATH,
    BRANCH_FOLIAGE_COLUMNS,
    CFS_DBH_HEIGHT_PATH,
    DEFAULT_ALLOMETRY_RECOVERY_FACTOR,
    SchemaError,
    compute_biomass_components,
    load_bc_biomass_allometry,
)
from workflows.national.bucking.national_bucking_pilot_v2 import DEFAULT_OUTPUT_DIR

REQUIRED_ARTIFACTS = (
    "tree_list.csv",
    "residue_ledger.csv",
    "bc_tipsy_residue.csv",
    "bc_residue_allometry.csv",
    "bc_residue_allometry_summary.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="national bucking pilot v2 allometry artifacts have not been generated",
)

BC_SPECIES = ("AC", "AT", "BL", "CW", "EP", "FD", "HM", "HW", "PL", "SB", "SE", "SW")
PRIOR_STEM_ODT_PER_M3 = 0.040576891708515125
FACILITY_GROSS_ODT_PER_M3 = 0.07622551414598874
FACILITY_RECOVERABLE_ODT_PER_M3 = 0.05424479172532829
GROSS_TOTAL_ODT_PER_M3 = 0.16162197619808732
RECOVERABLE_TOTAL_ODT_PER_M3 = 0.0969731857188524


@pytest.fixture(scope="module")
def table():
    return load_bc_biomass_allometry()


@pytest.fixture(scope="module")
def artifact() -> dict[str, object]:
    return {
        "trees": pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_list.csv"),
        "residue": pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv"),
        "tipsy": pd.read_csv(DEFAULT_OUTPUT_DIR / "bc_tipsy_residue.csv"),
        "components": pd.read_csv(DEFAULT_OUTPUT_DIR / "bc_residue_allometry.csv"),
        "comparison": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "bc_residue_allometry_summary.csv"
        ),
        "summary": json.loads(
            (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
        ),
        "manifest": json.loads(
            (DEFAULT_OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
        ),
    }


def test_equation_values_and_metadata(table) -> None:
    branch = table["SW"]["branch"]
    foliage = table["SW"]["foliage"]
    assert branch.a == pytest.approx(0.0322)
    assert branch.b == pytest.approx(2.8961)
    assert branch.c == pytest.approx(-0.9203)
    assert foliage.a == pytest.approx(0.1832)
    assert foliage.b == pytest.approx(2.4144)
    assert foliage.c == pytest.approx(-1.0948)
    assert branch.reference == "Ung 2008"
    assert branch.source_licence == "Open Government Licence - Canada"

    components = compute_biomass_components(
        tree_id="SW-1",
        species="SW",
        species_group="conifer",
        dbh_cm=21.0,
        height_m=15.2,
        table=table,
    )
    expected_branch = 0.0322 * 21.0**2.8961 * 15.2**-0.9203 / 1000.0
    expected_foliage = 0.1832 * 21.0**2.4144 * 15.2**-1.0948 / 1000.0
    assert components.branch_odt == pytest.approx(expected_branch, rel=1e-12)
    assert components.foliage_odt == pytest.approx(expected_foliage, rel=1e-12)
    assert components.branch_foliage_odt == pytest.approx(
        expected_branch + expected_foliage, rel=1e-12
    )
    assert components.evidence == "published_table"
    assert components.species_specific is True
    assert components.proxy is False


def test_all_bc_species_have_positive_components(table) -> None:
    frame = pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_list.csv")
    bc = frame[frame["jurisdiction"] == "BC"]
    assert set(bc["species"]) == set(BC_SPECIES)
    for row in bc.itertuples(index=False):
        components = compute_biomass_components(
            tree_id=row.tree_id,
            species=row.species,
            species_group=row.species_group,
            dbh_cm=row.dbh_cm,
            height_m=row.height_m,
            table=table,
        )
        assert components.branch_odt > 0.0
        assert components.foliage_odt > 0.0
        assert components.branch_foliage_odt > 0.0
        assert math.isfinite(components.branch_foliage_odt)


def test_group_fallback_is_explicit(table) -> None:
    mountain_hemlock = compute_biomass_components(
        tree_id="HM-1",
        species="HM",
        species_group="conifer",
        dbh_cm=12.8,
        height_m=6.9,
        table=table,
    )
    assert mountain_hemlock.equation_species == "Conifers"
    assert mountain_hemlock.proxy is True
    assert mountain_hemlock.species_specific is False

    black_cottonwood = compute_biomass_components(
        tree_id="AC-1",
        species="AC",
        species_group="deciduous",
        dbh_cm=20.0,
        height_m=15.0,
        table=table,
    )
    assert black_cottonwood.equation_species == "Red alder and Black cottonwood"
    assert black_cottonwood.species_specific is False
    assert black_cottonwood.proxy is False


def test_domain_and_invalid_inputs_fail_loud(table) -> None:
    with pytest.raises(SchemaError, match="outside the documented allometry domain"):
        compute_biomass_components(
            tree_id="bad-dbh",
            species="SW",
            species_group="conifer",
            dbh_cm=0.5,
            height_m=10.0,
            table=table,
        )
    with pytest.raises(SchemaError, match="outside the documented allometry domain"):
        compute_biomass_components(
            tree_id="bad-height",
            species="SW",
            species_group="conifer",
            dbh_cm=20.0,
            height_m=100.0,
            table=table,
        )
    with pytest.raises(SchemaError, match="height_m must exceed breast height"):
        compute_biomass_components(
            tree_id="bad-height-2",
            species="SW",
            species_group="conifer",
            dbh_cm=20.0,
            height_m=1.0,
            table=table,
        )


@requires_artifacts
def test_residue_component_identities(artifact) -> None:
    residue = artifact["residue"]
    bc = residue[residue["jurisdiction"] == "BC"]
    assert len(bc) == 347
    assert bc["branch_foliage_estimated"].all()
    assert (bc["residue_component_scope"] == "stem_plus_branch_foliage").all()
    assert bc["branch_foliage_evidence"].eq("published_table").all()
    assert bc["branch_foliage_equation_id"].str.len().gt(0).all()
    assert bc["branch_foliage_source_licence"].eq(
        "Open Government Licence - Canada"
    ).all()

    for row in bc.itertuples(index=False):
        assert row.stem_residue_odt == pytest.approx(row.residue_odt, rel=0, abs=0)
        assert row.branch_foliage_odt == pytest.approx(
            row.branch_odt + row.foliage_odt, rel=1e-12, abs=1e-12
        )
        assert row.total_residue_odt == pytest.approx(
            row.stem_residue_odt + row.branch_foliage_odt, rel=1e-12, abs=1e-12
        )
        assert row.recoverable_residue_odt == pytest.approx(
            row.total_residue_odt * DEFAULT_ALLOMETRY_RECOVERY_FACTOR,
            rel=1e-12,
            abs=1e-12,
        )
        assert row.stem_residue_odt_expanded == pytest.approx(
            row.stem_residue_odt * row.expansion_factor, rel=1e-12, abs=1e-12
        )
        assert row.total_residue_odt_expanded == pytest.approx(
            row.total_residue_odt * row.expansion_factor, rel=1e-12, abs=1e-12
        )

    other = residue[residue["jurisdiction"] != "BC"]
    assert not other["branch_foliage_estimated"].any()
    assert other["branch_odt"].isna().all()
    assert other["total_residue_odt"].isna().all()
    assert (
        other["residue_component_scope"]
        == "stem_only_branch_foliage_not_estimated"
    ).all()

    tipsy = artifact["tipsy"]
    assert tipsy["branch_foliage_estimated"].all()
    assert len(tipsy) == 347
    for row in tipsy.itertuples(index=False):
        assert row.total_residue_odt == pytest.approx(
            row.stem_residue_odt + row.branch_foliage_odt, rel=1e-12, abs=1e-12
        )


@requires_artifacts
def test_allometry_recomputes_from_committed_tree_list(artifact, table) -> None:
    trees = artifact["trees"]
    bc_trees = trees[trees["jurisdiction"] == "BC"]
    residue = artifact["residue"]
    national = residue[residue["jurisdiction"] == "BC"].set_index("tree_id")
    tipsy = artifact["tipsy"].set_index("tree_id")
    for row in bc_trees.itertuples(index=False):
        expected = compute_biomass_components(
            tree_id=row.tree_id,
            species=row.species,
            species_group=row.species_group,
            dbh_cm=row.dbh_cm,
            height_m=row.height_m,
            table=table,
        )
        assert national.loc[row.tree_id, "branch_odt"] == pytest.approx(
            expected.branch_odt, rel=1e-12, abs=1e-12
        )
        assert national.loc[row.tree_id, "foliage_odt"] == pytest.approx(
            expected.foliage_odt, rel=1e-12, abs=1e-12
        )
        assert tipsy.loc[row.tree_id, "branch_odt"] == pytest.approx(
            expected.branch_odt, rel=1e-12, abs=1e-12
        )
        assert tipsy.loc[row.tree_id, "foliage_odt"] == pytest.approx(
            expected.foliage_odt, rel=1e-12, abs=1e-12
        )


@requires_artifacts
def test_comparison_and_gaps(artifact) -> None:
    comparison = artifact["comparison"].set_index("variant")
    summary = artifact["summary"]["bc_residue_allometry"]

    assert len(comparison) == 8
    assert comparison.loc[
        "BC_V2_MEASURED_NATIONAL_SORTS_STEM_ONLY_PRIOR",
        "residue_odt_per_recovered_m3",
    ] == pytest.approx(PRIOR_STEM_ODT_PER_M3, rel=1e-12)
    assert comparison.loc[
        "BC_V2_MEASURED_NATIONAL_SORTS_GROSS_TOTAL",
        "residue_odt_per_recovered_m3",
    ] == pytest.approx(GROSS_TOTAL_ODT_PER_M3, rel=1e-9)
    assert comparison.loc[
        "BC_V2_MEASURED_NATIONAL_SORTS_RECOVERABLE_TOTAL",
        "residue_odt_per_recovered_m3",
    ] == pytest.approx(RECOVERABLE_TOTAL_ODT_PER_M3, rel=1e-9)
    assert comparison.loc[
        "BC_FACILITY_HARVEST_RESIDUE_GROSS", "residue_odt_per_recovered_m3"
    ] == pytest.approx(FACILITY_GROSS_ODT_PER_M3, rel=1e-12)
    assert comparison.loc[
        "BC_FACILITY_HARVEST_RESIDUE_RECOVERABLE", "residue_odt_per_recovered_m3"
    ] == pytest.approx(FACILITY_RECOVERABLE_ODT_PER_M3, rel=1e-12)

    gross = comparison.loc["BC_V2_MEASURED_NATIONAL_SORTS_GROSS_TOTAL"]
    recoverable = comparison.loc[
        "BC_V2_MEASURED_NATIONAL_SORTS_RECOVERABLE_TOTAL"
    ]
    assert gross["gap_vs_facility_gross_pct"] == pytest.approx(112.031336238, rel=1e-9)
    assert recoverable["gap_vs_facility_gross_pct"] == pytest.approx(
        27.218801743, rel=1e-9
    )
    assert gross["gap_vs_prior_stem_pct"] == pytest.approx(298.310391439, rel=1e-9)
    assert recoverable["gap_vs_prior_stem_pct"] == pytest.approx(
        138.986234864, rel=1e-9
    )
    assert summary["national_sorts"]["total_residue_odt_expanded"] == pytest.approx(
        summary["national_sorts"]["stem_residue_odt_expanded"]
        + summary["national_sorts"]["branch_foliage_odt_expanded"],
        rel=1e-12,
    )
    assert summary["recovery_factor"] == pytest.approx(
        DEFAULT_ALLOMETRY_RECOVERY_FACTOR
    )


@requires_artifacts
def test_labels_evidence_and_scope(artifact) -> None:
    components = artifact["components"]
    assert set(components["sort_rule_set"]) == {
        "national_pilot_sorts",
        "bc_interior_tipsy_sorts",
    }
    assert len(components) == 2 * 347
    assert components["model_estimate"].all()
    assert (~components["observed"]).all()
    assert (~components["policy_grade"]).all()
    assert (~components["national_claim"]).all()
    assert components["branch_foliage_evidence"].eq("published_table").all()
    assert components["measured_tree_list"].all()
    assert components["tree_list_label"].eq("measured_tree_list").all()

    summary = artifact["summary"]
    assert summary["labels"]["bc_branch_foliage_model_estimate"] is True
    assert summary["labels"]["bc_branch_foliage_evidence"] == "published_table"
    assert summary["labels"]["bc_stem_residue_separate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["national_claim"] is False
    assert summary["labels"]["ui_or_public_osecan_changed"] is False


@requires_artifacts
def test_manifest_records_allometry_sources(artifact) -> None:
    manifest = artifact["manifest"]
    assert manifest["inputs"]["bc_biomass_allometry_map"]["sha256"]
    assert manifest["inputs"]["cfs_biomass_equations_dbh_height"]["sha256"]
    assert manifest["inputs"]["cfs_biomass_equations_dbh"]["sha256"]
    assert (
        manifest["inputs"]["cfs_biomass_equations_dbh_height"]["licence"]
        == "Open Government Licence - Canada"
    )
    output_names = {record["path"] for record in manifest["outputs"]}
    assert "bc_residue_allometry.csv" in output_names
    assert "bc_residue_allometry_summary.csv" in output_names
    assert "workflows/national/bucking/bc_biomass_allometry.py" in manifest["code"]
    assert manifest["national_claim"] is False
    assert all(check["status"] == "PASS" for check in manifest["checks"])


def test_source_files_are_checksummed() -> None:
    readme = (CFS_DBH_HEIGHT_PATH.parent / "README.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(CFS_DBH_HEIGHT_PATH.read_bytes()).hexdigest()
    assert digest in readme
    assert ALLOMETRY_MAP_PATH.exists()
    frame = pd.read_csv(ALLOMETRY_MAP_PATH)
    assert set(frame["bc_species_code"]) >= set(BC_SPECIES)
    assert frame["domain_basis"].str.len().gt(0).all()


def test_component_columns_are_complete_by_construction(table) -> None:
    components = compute_biomass_components(
        tree_id="repeat",
        species="BL",
        species_group="conifer",
        dbh_cm=30.0,
        height_m=22.0,
        table=table,
    )
    first = components.to_dict()
    second = compute_biomass_components(
        tree_id="repeat",
        species="BL",
        species_group="conifer",
        dbh_cm=30.0,
        height_m=22.0,
        table=table,
    ).to_dict()
    assert first == second
    assert first["branch_foliage_odt"] == pytest.approx(
        first["branch_odt"] + first["foliage_odt"], rel=1e-12
    )


@requires_artifacts
def test_all_ledger_columns_are_present(artifact) -> None:
    for frame in (artifact["residue"], artifact["tipsy"], artifact["components"]):
        assert set(BRANCH_FOLIAGE_COLUMNS).issubset(frame.columns)
