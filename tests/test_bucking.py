"""Contract and conservation tests for the national bucking package."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.national.bucking.bucking import (
    BuckingError,
    buck_tree,
    buck_trees,
    load_grade_rules,
    load_price_profile,
)
from workflows.national.bucking.schema import BuckedLog, ResidueLedger, TreeRecord
from workflows.national.bucking.taper import (
    TaperRegistry,
    UnsupportedTaperError,
    default_kozak_parameter_path,
    default_nemora_root,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
PRICE_PATH = default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"


@pytest.fixture(scope="module")
def registry() -> TaperRegistry:
    return TaperRegistry(parameter_path=default_kozak_parameter_path())


@pytest.fixture(scope="module")
def rules():
    return load_grade_rules(RULES_PATH)


@pytest.fixture(scope="module")
def prices():
    return load_price_profile(PRICE_PATH)


def _tree(
    species: str,
    dbh_cm: float,
    height_m: float,
    *,
    bec_zone: str = "SBS",
    tree_id: str = "T1",
) -> TreeRecord:
    return TreeRecord(
        tree_id=tree_id,
        site_id="S1",
        species=species,
        dbh_cm=dbh_cm,
        height_m=height_m,
        status="live",
        expansion_factor=10.0,
        bec_zone=bec_zone,
        age=80.0,
        provenance="synthetic-test",
        plot_id="P1",
    )


def test_synthetic_tree_conserves_volume(registry, rules, prices):
    tree = _tree("PL", 30.0, 25.0)
    model = registry.resolve("BC", tree.species, tree.bec_zone)
    logs, residue = buck_tree(tree, model, rules, prices)

    assert logs, "a 30 cm / 25 m lodgepole pine should produce at least one log"
    recovered = sum(log.volume_m3 for log in logs)
    total = residue.taper_total_m3
    assert total is not None and total > 0
    assert recovered + residue.residue_volume_m3 == pytest.approx(total, rel=1e-9, abs=1e-9)
    assert recovered == pytest.approx(residue.recovered_log_m3, rel=1e-9, abs=1e-9)
    assert residue.residue_volume_m3 >= 0
    assert residue.bucker_balance_error_m3 is not None
    assert residue.bucker_balance_error_m3 <= max(1e-7, 1e-5 * total)


def test_no_negative_volumes_or_values(registry, rules, prices):
    tree = _tree("SW", 40.0, 30.0)
    model = registry.resolve("BC", tree.species, tree.bec_zone)
    logs, residue = buck_tree(tree, model, rules, prices)

    assert logs
    for log in logs:
        assert log.volume_m3 >= 0
        assert log.length_m > 0
        assert log.large_end_dib_cm >= 0
        assert log.small_end_dib_cm >= 0
        assert log.small_end_dib_cm <= log.large_end_dib_cm + 1e-6
        if log.price_cad_m3 is not None:
            assert log.price_cad_m3 >= 0
            assert log.value_cad == pytest.approx(
                log.volume_m3 * log.price_cad_m3, rel=1e-9, abs=1e-9
            )
    assert residue.residue_volume_m3 >= 0
    assert residue.residue_odt >= 0
    assert residue.green_tonnes >= 0


def test_species_and_sort_mapping(registry, rules, prices):
    from nemora.workflows.bucking import market_group_for

    assert market_group_for("SW") == "SPF"
    assert market_group_for("BL") == "Hem-Bal"
    assert market_group_for("AT") == "Deciduous"

    conifer_tree = _tree("PL", 35.0, 27.0, tree_id="C1")
    conifer_logs, _ = buck_tree(
        conifer_tree, registry.resolve("BC", "PL", "SBS"), rules, prices
    )
    assert conifer_logs
    conifer_codes = {rule.sort_code for rule in rules.for_species_class("conifer")}
    for log in conifer_logs:
        assert log.sort_code in conifer_codes
        assert log.product in {"sawlog", "pulpwood"}

    deciduous_tree = _tree("AT", 30.0, 25.0, tree_id="D1")
    deciduous_logs, _ = buck_tree(
        deciduous_tree, registry.resolve("BC", "AT", "SBS"), rules, prices
    )
    assert deciduous_logs
    deciduous_codes = {rule.sort_code for rule in rules.for_species_class("deciduous")}
    for log in deciduous_logs:
        assert log.sort_code in deciduous_codes
        assert log.product == "other"


def test_odt_and_green_tonne_conversion(registry, rules, prices):
    tree = _tree("BL", 25.0, 22.0)
    logs, residue = buck_tree(
        tree,
        registry.resolve("BC", "BL", "SBS"),
        rules,
        prices,
        basic_density_odt_m3=0.45,
        moisture_wet_basis=0.45,
        chip_yield_fraction=0.90,
    )
    assert logs
    assert residue.residue_odt == pytest.approx(residue.residue_volume_m3 * 0.45, rel=1e-9)
    assert residue.green_tonnes == pytest.approx(residue.residue_odt / 0.55, rel=1e-9)
    assert residue.chip_green_tonnes == pytest.approx(residue.green_tonnes * 0.90, rel=1e-9)
    assert residue.moisture_wet_basis == 0.45
    assert residue.chip_yield_fraction == 0.90


def test_small_tree_has_residue_and_no_logs(registry, rules, prices):
    tree = _tree("PL", 5.0, 4.0)
    logs, residue = buck_tree(tree, registry.resolve("BC", "PL", "SBS"), rules, prices)
    assert logs == []
    assert residue.residue_volume_m3 == pytest.approx(residue.taper_total_m3, rel=1e-9)
    assert residue.recovered_log_m3 == 0.0


def test_unsupported_taper_fails_loudly(registry):
    with pytest.raises(UnsupportedTaperError, match="no Kozak species mapping"):
        registry.resolve("BC", "W", "SBS")
    with pytest.raises(UnsupportedTaperError, match="no taper registered"):
        registry.resolve("ON", "PL", "SBS")
    with pytest.raises(UnsupportedTaperError, match="no Kozak location"):
        registry.resolve("BC", "PL", "XYZ")


def test_proxy_flag_is_explicit(registry):
    assert registry.resolve("BC", "SW", "SBS").proxy is True
    assert registry.resolve("BC", "PL", "SBS").proxy is False


def test_batch_skip_records_unsupported_trees(registry, rules, prices):
    trees = [_tree("PL", 30.0, 25.0, tree_id="OK"), _tree("W", 20.0, 18.0, tree_id="BAD")]
    batch = buck_trees(
        trees,
        registry,
        rules,
        prices,
        skip_invalid=True,
    )
    assert batch.logs
    assert any(row["tree_id"] == "BAD" for row in batch.skipped)
    assert "unsupported_taper" in next(
        row["reason"] for row in batch.skipped if row["tree_id"] == "BAD"
    )
    with pytest.raises(UnsupportedTaperError):
        buck_trees(trees, registry, rules, prices)


def test_contracts_reject_negative_or_inconsistent_rows():
    with pytest.raises(ValueError, match="volume_m3"):
        BuckedLog(
            tree_id="T1",
            log_index=1,
            length_m=5.0,
            large_end_dib_cm=20.0,
            small_end_dib_cm=15.0,
            volume_m3=-0.1,
            sort_code="D20L8",
            product="sawlog",
            price_cad_m3=100.0,
            value_cad=-10.0,
            evidence="published_table",
        )
    with pytest.raises(ValueError, match="moisture_wet_basis"):
        ResidueLedger(
            tree_id="T1",
            site_id="S1",
            residue_volume_m3=0.1,
            basic_density_odt_m3=0.45,
            residue_odt=0.045,
            moisture_wet_basis=1.2,
            green_tonnes=0.1,
            chip_yield_fraction=0.9,
            notes="bad moisture",
        )


def test_dead_tree_is_rejected(registry, rules, prices):
    tree = TreeRecord(
        tree_id="D1",
        site_id="S1",
        species="PL",
        dbh_cm=30.0,
        height_m=25.0,
        status="dead",
        expansion_factor=10.0,
        bec_zone="SBS",
        age=80.0,
        provenance="synthetic-test",
    )
    with pytest.raises(BuckingError, match="only live trees"):
        buck_tree(tree, registry.resolve("BC", "PL", "SBS"), rules, prices)
