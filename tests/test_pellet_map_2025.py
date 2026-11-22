"""Tests for the public Canadian Biomass 2025 pellet-map extract."""

from __future__ import annotations

import math

import pytest

from workflows.national.facilities import pellet_map_2025


def test_extract_has_43_current_producers() -> None:
    plants = pellet_map_2025.read_extract()
    assert len(plants) == 43
    assert [plant.map_row for plant in plants] == list(range(1, 44))
    assert {plant.province for plant in plants} == {
        "BC",
        "AB",
        "MB",
        "ON",
        "QC",
        "NB",
        "NS",
        "NL",
    }
    counts = summary_counts(plants)
    assert counts == {
        "BC": 11,
        "AB": 5,
        "MB": 2,
        "ON": 6,
        "QC": 11,
        "NB": 5,
        "NS": 2,
        "NL": 1,
    }


def summary_counts(plants) -> dict[str, int]:
    counts: dict[str, int] = {}
    for plant in plants:
        counts[plant.province] = counts.get(plant.province, 0) + 1
    return counts


def test_extract_capacity_totals_and_missing_value() -> None:
    plants = pellet_map_2025.read_extract()
    reported = sum(
        plant.capacity_tonnes_y
        for plant in plants
        if plant.capacity_tonnes_y is not None
    )
    converted = sum(
        plant.capacity_m3_y
        for plant in plants
        if plant.capacity_m3_y is not None
    )
    assert reported == 4_666_400
    assert converted == pytest.approx(reported / 0.65)
    missing = [plant for plant in plants if plant.capacity_tonnes_y is None]
    assert len(missing) == 1
    assert missing[0].name == "Skeena Bioenergy"
    assert missing[0].plant_status == "temporarily_closed"
    assert missing[0].capacity_m3_y is None


def test_conversion_uses_documented_density() -> None:
    assert pellet_map_2025.DENSITY_T_PER_M3 == 0.65
    assert (
        pellet_map_2025.DENSITY_MIN_T_PER_M3
        <= pellet_map_2025.DENSITY_T_PER_M3
        <= pellet_map_2025.DENSITY_MAX_T_PER_M3
    )
    assert pellet_map_2025.convert_tonnes_to_m3(650) == pytest.approx(1000.0)
    assert pellet_map_2025.convert_tonnes_to_m3(1000) == pytest.approx(
        1000 / 0.65
    )
    assert math.isfinite(pellet_map_2025.convert_tonnes_to_m3(72_000))


@pytest.mark.parametrize(
    "density", [0.0, 0.5, 0.8, -0.65, float("nan")]
)
def test_conversion_rejects_density_outside_documented_range(
    density: float,
) -> None:
    with pytest.raises(pellet_map_2025.PelletMapError):
        pellet_map_2025.convert_tonnes_to_m3(1000, density_t_per_m3=density)


@pytest.mark.parametrize("tonnes", [0.0, -1.0, float("inf")])
def test_conversion_rejects_non_positive_or_non_finite(tonnes: float) -> None:
    with pytest.raises(pellet_map_2025.PelletMapError):
        pellet_map_2025.convert_tonnes_to_m3(tonnes)


def test_proposed_plants_are_excluded() -> None:
    text = " ".join(plant.name.lower() for plant in pellet_map_2025.read_extract())
    for proposed in (
        "whitesand",
        "nairn centre",
        "wintergreen",
        "prairie clean energy",
    ):
        assert proposed not in text


def test_summary_labels_and_public_licence() -> None:
    payload = pellet_map_2025.summary(pellet_map_2025.read_extract())
    assert payload["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    assert payload["counts"]["plants"] == 43
    assert payload["counts"]["plants_with_capacity"] == 42
    assert payload["counts"]["temporarily_closed"] == 1
    assert payload["validation"]["no_restricted_data"] is True
    assert "Public PDF" in payload["licence"]
    assert "restricted" not in payload["licence"].lower()
    assert "proprietary" not in payload["licence"].lower()


def test_conversion_basis_is_finished_product_not_infeed() -> None:
    payload = pellet_map_2025.summary(pellet_map_2025.read_extract())
    assert "finished-pellet" in payload["method"]["conversion_basis"]
    assert "no pellet yield" in payload["method"]["conversion_basis"]
