"""Fail-closed and regression tests for the RSYC tile supply consumer."""

from __future__ import annotations

import csv
import math

import pytest

from workflows.national.clews import rsyc_tile_supply as supply
from workflows.national.clews.rsyc_tile_supply import (
    MatrixCell,
    ProvinceFactor,
    TileCell,
    TileSupplyError,
    aggregate_by_evidence,
    aggregate_by_province,
    aggregate_by_tile,
    allocate_province_factors,
    build_harvest_sensitivity,
    build_validation,
    load_backbone_harvest,
    load_backbone_province_factors,
    load_library,
    load_matrix_cells,
)
from workflows.national.rsyc_tile_contract import TileContractError

LIBRARY_FIELDS = [
    "cell_key",
    "tile_id",
    "province",
    "species",
    "species_kind",
    "evidence_tier",
    "allocation_basis",
    "area_ha",
    "no_province_intersection_area_ha",
    "b1",
    "b2",
    "b3",
    "b4",
    "volume_m3ha_age100",
]


def _library_row(**overrides: object) -> dict[str, object]:
    b1, b2, b3, b4 = 100.0, 0.05, 2.0, 0.01
    v100 = b1 * math.exp(-b4 * 100.0) * (1.0 - math.exp(-b2 * 100.0)) ** b3
    row: dict[str, object] = {
        "cell_key": "tile:T1|ON|PICE.MAR",
        "tile_id": "T1",
        "province": "ON",
        "species": "PICE.MAR",
        "species_kind": "species",
        "evidence_tier": "rsyc_tile_exact",
        "allocation_basis": "province_intersection",
        "area_ha": 10.0,
        "no_province_intersection_area_ha": 0.0,
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "b4": b4,
        "volume_m3ha_age100": v100,
    }
    row.update(overrides)
    return row


def _write_library(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIBRARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_load_library_accepts_valid_row_and_rejects_bad_rows(tmp_path) -> None:
    valid = tmp_path / "valid.csv"
    _write_library(valid, [_library_row()])
    assert len(load_library(valid)) == 1

    duplicate = tmp_path / "duplicate.csv"
    _write_library(duplicate, [_library_row(), _library_row()])
    with pytest.raises(TileSupplyError, match="duplicate cell_key"):
        load_library(duplicate)

    zero_area = tmp_path / "zero.csv"
    _write_library(zero_area, [_library_row(area_ha=0.0)])
    with pytest.raises(TileSupplyError, match="non-positive area"):
        load_library(zero_area)

    non_finite = tmp_path / "infinite.csv"
    _write_library(non_finite, [_library_row(b4=float("inf"))])
    with pytest.raises(TileSupplyError, match="non-finite"):
        load_library(non_finite)

    bad_shape = tmp_path / "shape.csv"
    _write_library(bad_shape, [_library_row(b1=0.0)])
    with pytest.raises(TileSupplyError, match="non-positive b1/b2/b3"):
        load_library(bad_shape)

    bad_key = tmp_path / "key.csv"
    _write_library(bad_key, [_library_row(cell_key="wrong")])
    with pytest.raises(TileSupplyError, match="non-canonical cell_key"):
        load_library(bad_key)

    unknown_tier = tmp_path / "tier.csv"
    _write_library(unknown_tier, [_library_row(evidence_tier="rsyc_made_up")])
    with pytest.raises(TileContractError, match="unknown evidence tier"):
        load_library(unknown_tier)

    unknown_basis = tmp_path / "basis.csv"
    _write_library(unknown_basis, [_library_row(allocation_basis="made_up")])
    with pytest.raises(TileContractError, match="unknown allocation basis"):
        load_library(unknown_basis)


MATRIX_FIELDS = [
    "cell_key",
    "record_type",
    "level",
    "stratum_name",
    "species",
    "area_weight_ha",
    "volume_m3",
    "residue_volume_m3",
    "residue_odt",
]


def _write_matrix(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _matrix_total(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cell_key": "ecoprovince:E|A",
        "record_type": "total",
        "level": "ecoprovince",
        "stratum_name": "E",
        "species": "A",
        "area_weight_ha": 10.0,
        "volume_m3": 100.0,
        "residue_volume_m3": 40.0,
        "residue_odt": 4.0,
    }
    row.update(overrides)
    return row


def test_matrix_loader_distinguishes_zero_log_from_missing_components(tmp_path) -> None:
    all_residue = tmp_path / "all_residue.csv"
    _write_matrix(
        all_residue,
        [
            _matrix_total(residue_volume_m3=100.0, residue_odt=10.0),
            {
                "cell_key": "ecoprovince:E|A",
                "record_type": "residue",
                "level": "ecoprovince",
                "stratum_name": "E",
                "species": "A",
                "area_weight_ha": "",
                "volume_m3": 100.0,
                "residue_volume_m3": "",
                "residue_odt": 10.0,
            },
        ],
    )
    cells = load_matrix_cells(all_residue)
    assert cells[0].log_volume_m3 == 0.0
    assert cells[0].residue_volume_m3 == 100.0

    missing_log = tmp_path / "missing_log.csv"
    _write_matrix(
        missing_log,
        [
            _matrix_total(),
            {
                "cell_key": "ecoprovince:E|A",
                "record_type": "residue",
                "level": "ecoprovince",
                "stratum_name": "E",
                "species": "A",
                "area_weight_ha": "",
                "volume_m3": 40.0,
                "residue_volume_m3": "",
                "residue_odt": 4.0,
            },
        ],
    )
    with pytest.raises(TileSupplyError, match="no log components"):
        load_matrix_cells(missing_log)

    duplicate = tmp_path / "duplicate.csv"
    _write_matrix(duplicate, [_matrix_total(), _matrix_total()])
    with pytest.raises(TileSupplyError, match="duplicate total"):
        load_matrix_cells(duplicate)


def _factor(province: str, total: float, log: float, residue: float) -> ProvinceFactor:
    return ProvinceFactor(
        province=province,
        total_volume_m3=total,
        log_volume_m3=log,
        residue_odt=residue,
        cells=1,
    )


def _cell(tile: str, province: str, area: float = 10.0, v100: float = 100.0, tier: str = "rsyc_tile_exact") -> TileCell:
    return TileCell(
        tile_id=tile,
        province=province,
        species="PICE.MAR",
        species_kind="species",
        evidence_tier=tier,
        area_ha=area,
        v100_m3ha=v100,
    )


def _backbone_harvest() -> dict[tuple[int, str], dict[str, float]]:
    return {
        (level, case): {"recoverable_pj_per_year": 100.0}
        for level in supply.HARVEST_LEVELS_MILLION_M3
        for case in supply.CASES
    }


def test_two_province_total_and_log_denominators_and_tile_mix() -> None:
    matrix = [
        MatrixCell(
            cell_key="ecoprovince:E1|A",
            ecoprovince="E1",
            species="A",
            area_ha=100.0,
            total_volume_m3=1000.0,
            log_volume_m3=600.0,
            residue_volume_m3=400.0,
            residue_odt=200.0,
        ),
        MatrixCell(
            cell_key="ecoprovince:E2|B",
            ecoprovince="E2",
            species="B",
            area_ha=100.0,
            total_volume_m3=1000.0,
            log_volume_m3=700.0,
            residue_volume_m3=300.0,
            residue_odt=100.0,
        ),
    ]
    species_area = {
        ("E1", "A"): {"P1": 100.0},
        ("E2", "B"): {"P2": 100.0},
    }
    factors = allocate_province_factors(matrix, species_area)
    assert factors["P1"].odt_per_m3_total == pytest.approx(0.2)
    assert factors["P1"].odt_per_m3_log == pytest.approx(200.0 / 600.0)
    assert factors["P2"].odt_per_m3_total == pytest.approx(0.1)
    assert factors["P2"].odt_per_m3_log == pytest.approx(100.0 / 700.0)

    cells = [_cell("T1", "P1"), _cell("T2", "P2")]
    province_rows = aggregate_by_province(
        cells,
        factors,
        {"P1": {"residue_odt": 1.0, "log_volume_m3": 1.0, "odt_per_m3_log": 1.0},
         "P2": {"residue_odt": 1.0, "log_volume_m3": 1.0, "odt_per_m3_log": 1.0}},
    )
    tile_rows = aggregate_by_tile(cells, factors, {"T1": 100.0, "T2": 100.0})
    assert sum(row["residue_odt"] for row in province_rows) == pytest.approx(300.0)
    assert sum(row["residue_odt"] for row in tile_rows) == pytest.approx(300.0)
    tile_factor = sum(row["residue_odt"] for row in tile_rows) / 2000.0
    matrix_factor = 300.0 / 2000.0
    assert tile_factor == pytest.approx(0.15)
    assert matrix_factor == pytest.approx(0.15)


def test_harvest_scenarios_publish_both_bases_and_tile_factor() -> None:
    rows = build_harvest_sensitivity(
        tile_mix_factor=0.08,
        matrix_factor=0.09,
        national_residue_odt=1_000_000_000.0,
        backbone_harvest=_backbone_harvest(),
    )
    assert len(rows) == 24
    tile_100 = next(
        row
        for row in rows
        if row["scenario"] == "tile_mix"
        and row["harvest_level_million_m3_per_year"] == 100
        and row["case"] == "base"
    )
    matrix_100 = next(
        row
        for row in rows
        if row["scenario"] == "matrix_basis"
        and row["harvest_level_million_m3_per_year"] == 100
        and row["case"] == "base"
    )
    assert tile_100["harvest_volume_basis"] == "total_volume_m3_per_year"
    assert tile_100["residue_factor_odt_per_m3"] == pytest.approx(0.08)
    assert tile_100["recoverable_pj_per_year"] == pytest.approx(88.8)
    assert matrix_100["residue_factor_odt_per_m3"] == pytest.approx(0.09)
    assert matrix_100["recoverable_pj_per_year"] == pytest.approx(99.9)
    assert tile_100["hypothetical_all_hectares_age100_stock_pj_equivalent"] == pytest.approx(
        1_000_000_000.0 * 0.6 * 18.5 / 1_000_000.0
    )


def test_b25_over_capacity_is_flagged_not_clipped() -> None:
    factors = {"P1": _factor("P1", 1000.0, 600.0, 100.0)}
    rows = aggregate_by_tile([_cell("B25", "P1", area=120.0)], factors, {"B25": 100.0})
    assert rows[0]["area_ha"] == pytest.approx(120.0)
    assert rows[0]["allocated_to_polygon_area_ratio"] == pytest.approx(1.2)
    assert rows[0]["excess_area_ha"] == pytest.approx(20.0)
    assert rows[0]["over_capacity"] is True


def test_evidence_table_has_four_tiers_and_independent_rollups(monkeypatch) -> None:
    tiers = list(supply.EVIDENCE_TIERS)
    cells = [
        _cell("B25", "P1", area=120.0, tier=tiers[0]),
        _cell("T1", "P1", area=10.0, tier=tiers[1]),
        _cell("T2", "P1", area=10.0, tier=tiers[2]),
        _cell("T3", "P1", area=10.0, tier=tiers[3]),
    ]
    factors = {"P1": _factor("P1", 1000.0, 600.0, 100.0)}
    backbone = {"P1": {"residue_odt": 100.0, "log_volume_m3": 600.0, "odt_per_m3_log": 1.0 / 6.0}}
    tile_areas = {"B25": 100.0, "T1": 100.0, "T2": 100.0, "T3": 100.0}
    province_rows = aggregate_by_province(cells, factors, backbone)
    tile_rows = aggregate_by_tile(cells, factors, tile_areas)
    evidence_rows = aggregate_by_evidence(cells, factors)
    assert [row["evidence_tier"] for row in evidence_rows] == tiers
    assert sum(row["area_share"] for row in evidence_rows) == pytest.approx(1.0)
    assert sum(row["recoverable_pj_share"] for row in evidence_rows) == pytest.approx(1.0)

    monkeypatch.setattr(supply, "EXPECTED_AREA_HA", 150.0)
    monkeypatch.setattr(supply, "DIAGNOSTIC_RESIDUE_ODT", 100.0)
    monkeypatch.setattr(supply, "DIAGNOSTIC_TOTAL_VOLUME_M3", 1000.0)
    harvest_rows = build_harvest_sensitivity(
        0.1, 0.1, 1500.0, _backbone_harvest()
    )
    checks = build_validation(
        cells,
        province_rows,
        tile_rows,
        evidence_rows,
        harvest_rows,
        factors,
        backbone,
        tile_areas,
    )
    assert all(check["status"] == "PASS" for check in checks), [
        check for check in checks if check["status"] != "PASS"
    ]

    province_rows[0]["area_ha"] += 1.0
    broken = build_validation(
        cells,
        province_rows,
        tile_rows,
        evidence_rows,
        harvest_rows,
        factors,
        backbone,
        tile_areas,
    )
    failed = {check["check"] for check in broken if check["status"] != "PASS"}
    assert "province_rollup_reconciles_independently" in failed


HARVEST_FIELDS = [
    "harvest_level_million_m3_per_year",
    "case",
    "gross_residue_odt_per_year",
    "recoverable_residue_odt_per_year",
    "recoverable_pj_per_year",
    "annual_cost_million_usd",
]


def _harvest_row(level: int = 150, case: str = "base", pj: object = 100.0) -> dict[str, object]:
    return {
        "harvest_level_million_m3_per_year": level,
        "case": case,
        "gross_residue_odt_per_year": 1_000_000.0,
        "recoverable_residue_odt_per_year": 600_000.0,
        "recoverable_pj_per_year": pj,
        "annual_cost_million_usd": 245.6977549,
    }


def _write_harvest(path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HARVEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_backbone_province_factors_reject_duplicate_keys(tmp_path) -> None:
    path = tmp_path / "province_factors.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["province", "residue_odt", "log_volume_m3", "odt_per_m3_log"],
        )
        writer.writeheader()
        for _ in range(2):
            writer.writerow(
                {
                    "province": "ON",
                    "residue_odt": 1.0,
                    "log_volume_m3": 2.0,
                    "odt_per_m3_log": 0.5,
                }
            )
    with pytest.raises(TileSupplyError, match="duplicate province factor key"):
        load_backbone_province_factors(path)


def test_backbone_harvest_rejects_duplicate_keys(tmp_path) -> None:
    path = tmp_path / "harvest.csv"
    _write_harvest(path, [_harvest_row(), _harvest_row()])
    with pytest.raises(TileSupplyError, match="duplicate harvest comparison key"):
        load_backbone_harvest(path)


def test_backbone_harvest_rejects_non_finite_comparison_values(tmp_path) -> None:
    path = tmp_path / "harvest.csv"
    _write_harvest(path, [_harvest_row(pj=float("inf"))])
    with pytest.raises(TileSupplyError, match="non-finite recoverable_pj_per_year"):
        load_backbone_harvest(path)


def test_backbone_harvest_parses_finite_rows(tmp_path) -> None:
    path = tmp_path / "harvest.csv"
    _write_harvest(path, [_harvest_row()])
    rows = load_backbone_harvest(path)
    assert rows[(150, "base")]["recoverable_pj_per_year"] == pytest.approx(100.0)
