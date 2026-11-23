"""Regression tests for the national truck transport cost component v1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from workflows.national.facilities import transport_cost_v1 as transport

OUTPUT_DIR = transport.OUTPUT_DIR
PARAMETERS_CSV = OUTPUT_DIR / "parameters.csv"
CURVES_CSV = OUTPUT_DIR / "curves.csv"
COMPARISON_CSV = OUTPUT_DIR / "supply_curve_comparison.csv"
CROSSINGS_CSV = OUTPUT_DIR / "supply_curve_crossings.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
PROVENANCE_CSV = OUTPUT_DIR / "provenance.csv"
REPORT_MD = OUTPUT_DIR / "TRANSPORT_COST_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def summary() -> dict[str, Any]:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def curves() -> list[dict[str, str]]:
    return _read_csv(CURVES_CSV)


@pytest.fixture(scope="module")
def parameters() -> list[dict[str, str]]:
    return _read_csv(PARAMETERS_CSV)


@pytest.fixture(scope="module")
def comparison() -> list[dict[str, str]]:
    return _read_csv(COMPARISON_CSV)


@pytest.fixture(scope="module")
def crossings() -> list[dict[str, str]]:
    return _read_csv(CROSSINGS_CSV)


def test_outputs_exist_and_manifest_hashes() -> None:
    for path in (
        PARAMETERS_CSV,
        CURVES_CSV,
        COMPARISON_CSV,
        CROSSINGS_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        PROVENANCE_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "transport_cost_v1"
    assert manifest["artifact_version"] == "1.0.0"
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert _sha256(path) == entry["sha256"], name


def test_curve_grid_and_units(curves: list[dict[str, str]]) -> None:
    assert len(curves) == (
        len(transport.COST_SCENARIOS)
        * len(transport.DISTANCES_KM)
        * len(transport.PAYLOADS_M3)
    )
    assert {float(row["distance_km_one_way"]) for row in curves} == set(
        transport.DISTANCES_KM
    )
    assert {float(row["payload_m3"]) for row in curves} == set(
        transport.PAYLOADS_M3
    )
    assert {row["cost_scenario"] for row in curves} == set(
        transport.COST_SCENARIOS
    )
    for row in curves:
        distance = float(row["distance_km_one_way"])
        payload = float(row["payload_m3"])
        total = float(row["total_cost_cad"])
        assert float(row["round_trip_km"]) == pytest.approx(2.0 * distance)
        assert float(row["loaded_km_share"]) == pytest.approx(0.5)
        assert float(row["cost_per_m3_cad"]) == pytest.approx(
            total / payload, abs=1e-6
        )
        assert float(row["cost_per_odt_cad"]) == pytest.approx(
            total / float(row["payload_odt"]), abs=1e-6
        )
        assert float(row["cost_per_odt_km_cad"]) == pytest.approx(
            float(row["cost_per_odt_cad"]) / distance, abs=1e-6
        )
        assert float(row["cost_per_gj_cad"]) == pytest.approx(
            total / float(row["energy_gj"]), abs=1e-6
        )
        assert float(row["payload_odt"]) == pytest.approx(
            payload * float(summary_density(row)), abs=1e-9
        )


def summary_density(row: dict[str, str]) -> float:
    # The density is not repeated per row; recover it from payload volume/ODT.
    return float(row["payload_odt"]) / float(row["payload_m3"])


def test_component_arithmetic(curves: list[dict[str, str]]) -> None:
    for row in curves:
        components = math.fsum(
            float(row[field])
            for field in (
                "fuel_cost_cad",
                "driver_cost_cad",
                "maintenance_cost_cad",
                "tire_cost_cad",
                "capital_cost_cad",
                "insurance_cost_cad",
                "overhead_cost_cad",
            )
        )
        assert components == pytest.approx(float(row["total_cost_cad"]), abs=1e-6)
        per_m3 = math.fsum(
            float(row[field])
            for field in (
                "fuel_per_m3_cad",
                "driver_per_m3_cad",
                "maintenance_per_m3_cad",
                "tire_per_m3_cad",
                "capital_per_m3_cad",
                "insurance_per_m3_cad",
                "overhead_per_m3_cad",
            )
        )
        assert per_m3 == pytest.approx(float(row["cost_per_m3_cad"]), abs=1e-6)


def test_no_nan_or_negative(curves: list[dict[str, str]]) -> None:
    numeric_fields = [
        key
        for key in curves[0]
        if key
        not in {
            "cost_scenario",
            "mass_limit_binding",
            "long_haul_flag",
            "evidence_label",
            "observed",
            "policy_grade",
            "assignment_change",
            "national_claim",
        }
    ]
    for row in curves:
        for field in numeric_fields:
            value = float(row[field])
            assert math.isfinite(value), (field, row["cost_scenario"])
            assert value >= 0.0, (field, row["cost_scenario"])


def test_cost_scenarios_are_ordered(curves: list[dict[str, str]]) -> None:
    grouped: dict[tuple[float, float], dict[str, dict[str, str]]] = {}
    for row in curves:
        grouped.setdefault(
            (float(row["distance_km_one_way"]), float(row["payload_m3"])), {}
        )[row["cost_scenario"]] = row
    for key, rows in grouped.items():
        assert float(rows["low"]["cost_per_m3_cad"]) <= float(
            rows["base"]["cost_per_m3_cad"]
        ) <= float(rows["high"]["cost_per_m3_cad"]), key
        assert float(rows["low"]["cost_per_gj_cad"]) <= float(
            rows["base"]["cost_per_gj_cad"]
        ) <= float(rows["high"]["cost_per_gj_cad"]), key


def test_payload_monotonicity(curves: list[dict[str, str]]) -> None:
    for scenario in transport.COST_SCENARIOS:
        for distance in transport.DISTANCES_KM:
            values = {
                float(row["payload_m3"]): float(row["cost_per_m3_cad"])
                for row in curves
                if row["cost_scenario"] == scenario
                and float(row["distance_km_one_way"]) == distance
            }
            assert values[45.0] > values[52.5] > values[60.0]


def test_parameters_units_and_bounds(parameters: list[dict[str, str]]) -> None:
    by_id = {row["parameter_id"]: row for row in parameters}
    for parameter_id in (
        "diesel_cad_per_l",
        "driver_wage_cad_per_h",
        "fuel_loaded_l_per_km",
        "fuel_empty_l_per_km",
        "maintenance_cad_per_km",
        "tire_cad_per_km",
        "insurance_rate_of_capital_per_year",
        "overhead_fraction",
        "payload_m3",
        "residue_bulk_density_odt_per_m3",
        "residue_lhv_gj_per_odt",
    ):
        row = by_id[parameter_id]
        assert row["unit"]
        assert float(row["low"]) <= float(row["base"]) <= float(row["high"])
        assert row["source_id"]
        assert row["licence"]
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    assert by_id["diesel_cad_per_l"]["source_id"] == "statcan_18100001_diesel"
    assert (
        by_id["driver_wage_cad_per_h"]["source_id"]
        == "statcan_14100417_wages_occupation"
    )
    assert float(by_id["payload_m3"]["low"]) == 45.0
    assert float(by_id["payload_m3"]["base"]) == 52.5
    assert float(by_id["payload_m3"]["high"]) == 60.0


def test_supply_comparison_arithmetic(
    comparison: list[dict[str, str]],
    summary: dict[str, Any],
) -> None:
    assert len(comparison) == (
        len(transport.COST_SCENARIOS)
        * len(transport.DISTANCES_KM)
        * len(transport.PAYLOADS_M3)
        * 4
    )
    for row in comparison:
        transport_cost = float(row["transport_cost_cad_per_gj"])
        tier_cost = float(row["tier_cost_per_gj"])
        ratio = float(row["ratio_transport_to_tier"])
        assert ratio == pytest.approx(transport_cost / tier_cost, abs=1e-9)
        assert float(row["ratio_minus_one"]) == pytest.approx(ratio - 1.0, abs=1e-9)
        assert row["transport_exceeds_tier"] == (
            "true" if transport_cost > tier_cost else "false"
        )
        assert row["currency_basis"]
    assert summary["supply_curve"]["comparison_rows"] == len(comparison)


def test_crossings_are_finite_and_ordered(
    crossings: list[dict[str, str]],
) -> None:
    assert len(crossings) == len(transport.COST_SCENARIOS) * len(
        transport.PAYLOADS_M3
    ) * 4
    grouped: dict[tuple[str, float], dict[str, float]] = {}
    for row in crossings:
        crossing = float(row["crossing_distance_km"])
        assert math.isfinite(crossing)
        assert crossing >= 0.0
        grouped.setdefault(
            (row["cost_scenario"], float(row["payload_m3"])), {}
        )[row["tier"]] = crossing
    for key, values in grouped.items():
        assert values["T1"] == pytest.approx(values["T2"], abs=1e-9)
        assert values["T1"] <= values["T3"] <= values["T4"], key


def test_mass_screen(curves: list[dict[str, str]]) -> None:
    for row in curves:
        assert row["mass_limit_binding"] == "false"
        assert float(row["payload_wet_kg"]) <= float(row["legal_payload_kg"])
        assert float(row["payload_wet_kg"]) > 0.0


def test_labels_and_context(summary: dict[str, Any]) -> None:
    assert summary["artifact"] == "transport_cost_v1"
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "assignment_change": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
        "model_scope": "national_scope_component_not_national_observed_average",
    }
    assert summary["validation"]["all_passed"] is True
    assert all(summary["validation"]["checks"].values())
    assert summary["facility_context"]["status"] == "context_only"
    assert summary["facility_context"]["n_unique_facilities"] > 0
    assert summary["road_distance_context"]["status"] == "context_only"
    assert summary["scope"]["return_leg"] == "empty"
    assert summary["scope"]["currency"] == "CAD"
    assert summary["scope"]["price_year"] == 2026


def test_report_and_purity() -> None:
    text = REPORT_MD.read_text(encoding="utf-8")
    assert "model estimate" in text
    assert "no official OSeCAN" in text
    assert "no assignment" in text
    assert "Supply-curve comparison" in text
    assert "Limits" in text
    parameters = transport.build_parameters(
        transport.load_open_observations(), transport.load_capital_bounds()
    )
    parameter_map = transport._parameter_map(parameters)
    first = transport.trip_cost(100.0, 52.5, parameter_map, "base")
    second = transport.trip_cost(100.0, 52.5, parameter_map, "base")
    assert first == second
    assert first["total_cost_cad"] > 0.0
    zero = transport.trip_cost(0.0, 52.5, parameter_map, "base")
    assert zero["fuel_cost_cad"] == 0.0
    assert zero["driver_cost_cad"] > 0.0
    assert zero["cost_per_odt_km_cad"] == 0.0


def test_parameter_fixed_direction_is_base() -> None:
    parameter = transport.Parameter(
        parameter_id="fixed_test",
        component="test",
        unit="unit",
        low=1.0,
        base=2.0,
        high=3.0,
        direction="fixed",
        source_id="model_assumptions_cc0",
        notes="test",
    )
    assert parameter.value_for("low") == 2.0
    assert parameter.value_for("base") == 2.0
    assert parameter.value_for("high") == 2.0


def test_deletion_parser_fails_closed() -> None:
    text = (
        " M workflows/x.py\n"
        " D data/file.csv\n"
        "D  data/other.csv\n"
        "?? data/new.csv\n"
        "M  data/submodule"
    )
    deletions = transport.parse_deletion_lines(text)
    assert len(deletions) == 2
    assert any(line.startswith(" D") for line in deletions)
    assert any(line.startswith("D ") for line in deletions)
