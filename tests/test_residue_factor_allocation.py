"""Harvest-weighted residue factors and allocation-aware runner caps."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.national.harvest.residue_factor_allocation import (
    ALLOCATION_TOTALS_M3,
    LHV_GJ_PER_ODT,
    RECOVERY,
    ResidueFactorError,
    load_allocation_harvest,
    load_province_factors,
    weighted_residue_factor,
)
from workflows.ui.contracts import parse_scenario
from workflows.ui.osecan_runner import (
    CANONICAL_SET_PATH,
    RunnerError,
    compute_supply_caps,
    load_canonical_set,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def scenario_payload(*, scenario_id: str, level_m3: float, allocation: str | None) -> dict:
    harvest = {
        "level_m3_per_year": level_m3,
        "path": "custom",
        "recovery_fraction": 0.6,
        "cost_basis": "adopted_rest_of_canada_curve",
        "supply_curve": "tiered",
    }
    if allocation is not None:
        harvest["allocation"] = allocation
    return {
        "scenario_id": scenario_id,
        "label": f"Screening {level_m3:.0f} m3",
        "schema_version": "clews.scenario.v1",
        "harvest": harvest,
        "energy": {
            "carbon_price_schedule": [
                {"year": 2021, "price_usd_per_tco2e": 95.0},
                {"year": 2026, "price_usd_per_tco2e": 200.0},
            ],
            "hydro_mode": "cap_2021",
            "pwr_bio_cap_fraction": 0.05,
            "nuclear_cap_gw": 0.0,
            "wind_cost_usd_per_gj": 3.0,
            "wind_cap_fraction": 0.2,
            "t3_cap_pj": 39.873714,
            "t4_cap_pj": 64.265464,
            "generic_bio_cost_usd_per_gj": 3.0,
        },
        "constraints": {"set_id": "screening_v0"},
        "provenance": {
            "base_data_commit": "test",
            "model_commit": "test",
            "created_utc": "2026-11-20T00:00:00+00:00",
        },
    }


def _factor(province: str, gross: float):
    from workflows.national.harvest.residue_factor_allocation import (
        ProvinceResidueFactor,
    )

    return ProvinceResidueFactor(
        province=province,
        gross_odt_per_m3_log=gross,
        recoverable_odt_per_m3_low=gross * RECOVERY["low"],
        recoverable_odt_per_m3_base=gross * RECOVERY["base"],
        recoverable_odt_per_m3_high=gross * RECOVERY["high"],
    )


def test_weighted_factor_is_harvest_weighted_not_area_weighted() -> None:
    factors = {"A": _factor("A", 0.10), "B": _factor("B", 0.20)}
    harvest = {"A": 75.0, "B": 25.0}

    weighted = weighted_residue_factor(harvest, factors, case="test")

    assert weighted.total_harvest_m3 == pytest.approx(100.0)
    assert weighted.gross_odt_per_m3_log == pytest.approx(0.125)
    assert weighted.odt_per_m3_log_base == pytest.approx(0.125 * 0.6)
    assert weighted.pj_per_100m_log_base == pytest.approx(
        0.125 * 0.6 * 1e8 * LHV_GJ_PER_ODT / 1_000_000
    )
    assert weighted.national_total_pj_base == pytest.approx(
        0.125 * 0.6 * 100.0 * LHV_GJ_PER_ODT / 1_000_000
    )


def test_all_allocation_cases_conserve_documented_totals() -> None:
    for case, total in ALLOCATION_TOTALS_M3.items():
        harvest = load_allocation_harvest(case)
        assert sum(harvest.values()) == pytest.approx(total, abs=2.0)
        for value in harvest.values():
            assert value >= 0


def test_missing_province_factor_fails_closed() -> None:
    factors = load_province_factors()
    del factors["BC"]
    harvest = load_allocation_harvest("119m")

    with pytest.raises(ResidueFactorError, match="missing province residue factors.*BC"):
        weighted_residue_factor(harvest, factors, case="119m")


def test_missing_allocation_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ResidueFactorError, match="allocation file is missing"):
        load_allocation_harvest("119m", allocation_dir=tmp_path)


def test_allocation_totals_must_match(tmp_path: Path) -> None:
    source = REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
    target = tmp_path / "119m.csv"
    target.write_text(
        source.read_text(encoding="utf-8").replace(
            "39978577.0", "30000000.0", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ResidueFactorError, match="allocation totals"):
        load_allocation_harvest("119m", allocation_dir=tmp_path)


def test_lp_allocation_lowers_national_cap_and_changes_supbcwt() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    area = compute_supply_caps(
        parse_scenario(scenario_payload(
            scenario_id="area_share", level_m3=118_977_339.0, allocation=None
        )),
        canonical,
    )
    allocated = compute_supply_caps(
        parse_scenario(scenario_payload(
            scenario_id="lp_119m", level_m3=118_977_339.0, allocation="lp_119m"
        )),
        canonical,
    )

    assert allocated.harvest_allocation == "119m"
    assert allocated.residue_factor_odt_per_m3_log < area.residue_factor_odt_per_m3_log
    assert allocated.national_pj_by_year[2050] < area.national_pj_by_year[2050]
    assert allocated.rest_of_canada_pj_by_year[2050] < area.rest_of_canada_pj_by_year[2050]
    assert (
        allocated.tier_caps_pj_by_year["SUPBCWT2"][2050]
        < area.tier_caps_pj_by_year["SUPBCWT2"][2050]
    )
    assert allocated.checks["allocation"]["basis"] == "harvest_allocation"
    assert allocated.checks["allocation"]["allocation_case"] == "119m"
    assert allocated.checks["allocation"]["allocation_total_matches_harvest_from_2025"] is True
    assert allocated.checks["tier_sum_equals_rest_all_years"] is True
    assert allocated.checks["national_equals_tiers_plus_bc_all_years"] is True


def test_none_allocation_preserves_area_share() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    absent = compute_supply_caps(
        parse_scenario(scenario_payload(
            scenario_id="absent", level_m3=118_977_339.0, allocation=None
        )),
        canonical,
    )
    explicit = compute_supply_caps(
        parse_scenario(scenario_payload(
            scenario_id="explicit_none", level_m3=118_977_339.0, allocation="none"
        )),
        canonical,
    )
    assert absent.harvest_allocation is None
    assert explicit.harvest_allocation is None
    assert absent.national_pj_by_year == explicit.national_pj_by_year
    assert absent.residue_factor_odt_per_m3_log == pytest.approx(
        canonical.tile_factor_odt_per_m3 / canonical.log_share
    )


def test_runner_wraps_missing_allocation_inputs_as_runner_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workflows.ui.osecan_runner as runner

    def fail(*args, **kwargs):
        raise ResidueFactorError("missing province residue factors for ['BC']")

    monkeypatch.setattr(runner, "allocation_residue_factor", fail)
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    scenario = parse_scenario(
        scenario_payload(scenario_id="bad_alloc", level_m3=118_977_339.0, allocation="lp_119m")
    )
    with pytest.raises(RunnerError, match="missing province residue factors"):
        compute_supply_caps(scenario, canonical)
