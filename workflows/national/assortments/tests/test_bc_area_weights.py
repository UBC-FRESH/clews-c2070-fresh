"""Tests for the BC forest/THLB area-weight table."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.assortments.bc_area_weights import (
    UNMAPPED_SPECIES,
    aggregate_area_table,
    build_batch_join,
    build_species_crosswalk,
    map_bc_species,
    normalise_species_percentages,
    thlb_area_from_percent,
)


def test_species_percentages_normalise_and_zero_rows_are_invalid() -> None:
    weights, valid = normalise_species_percentages(
        np.array([[60.0, 40.0, 0.0], [25.0, 25.0, 50.0], [0.0, 0.0, 0.0]])
    )
    assert valid.tolist() == [True, True, False]
    np.testing.assert_allclose(weights[0], [0.6, 0.4, 0.0])
    np.testing.assert_allclose(weights[1], [0.25, 0.25, 0.5])
    np.testing.assert_allclose(weights[2], [0.0, 0.0, 0.0])


def test_species_percentages_treat_negative_and_non_finite_as_zero() -> None:
    weights, valid = normalise_species_percentages(
        np.array([[np.nan, -10.0, 100.0], [0.0, 0.0, 0.0]])
    )
    assert valid.tolist() == [True, False]
    np.testing.assert_allclose(weights[0], [0.0, 0.0, 1.0])


def test_thlb_area_multiplies_and_treats_nodata_as_zero() -> None:
    result = thlb_area_from_percent(
        np.array([100.0, 100.0, 100.0, 100.0]),
        np.array([100.0, 50.0, -9999.0, 150.0]),
    )
    np.testing.assert_allclose(result, [100.0, 50.0, 0.0, 0.0])


def test_crosswalk_maps_exact_species_and_flags_unmapped() -> None:
    crosswalk = pd.DataFrame(
        {
            "bc_species_codes": ["FDC", "FDI", "PLI", "SX", "XX"],
            "casfri_species_codes": [
                "PSEU_MEN_MEN",
                "PSEU_MEN_GLA",
                "PINU_CON_LAT",
                "PICE_HYB_###",
                "UNKN_SPP_###",
            ],
        }
    )
    code_to_rsyc, _ = build_species_crosswalk(crosswalk)
    assert map_bc_species("FDC", code_to_rsyc) == ("PSEU.MEN", "exact_crosswalk")
    assert map_bc_species("fdi", code_to_rsyc) == ("PSEU.MEN", "exact_crosswalk")
    assert map_bc_species("PLI", code_to_rsyc) == ("PINU.CON", "exact_crosswalk")
    assert map_bc_species("SX", code_to_rsyc) == (UNMAPPED_SPECIES, "unmapped")
    assert map_bc_species("missing", code_to_rsyc) == (UNMAPPED_SPECIES, "unmapped")


def test_area_aggregation_sums_area_thlb_and_counts() -> None:
    rows = pd.DataFrame(
        {
            "tsa_number": ["24", "24", "24"],
            "tsa_name": ["Prince George", "Prince George", "Prince George"],
            "ecoprovince": ["Central Montane Cordillera"] * 3,
            "ecoregion": ["Fraser Basin"] * 3,
            "rsyc_species": ["PICE.ENG", "PICE.ENG", "PINU.CON"],
            "area_ha": [10.0, 5.0, 2.0],
            "thlb_area_ha": [4.0, 2.0, 1.0],
        }
    )
    table = aggregate_area_table(rows)
    pice = table[table["rsyc_species"] == "PICE.ENG"].iloc[0]
    assert pice["area_ha"] == pytest.approx(15.0)
    assert pice["thlb_area_ha"] == pytest.approx(6.0)
    assert pice["n_polygons"] == 2


def test_batch_join_scales_per_ha_and_flags_no_area(tmp_path: Path) -> None:
    area_table = pd.DataFrame(
        {
            "ecoprovince": ["Central Montane Cordillera", "Central Montane Cordillera"],
            "rsyc_species": ["PICE.ENG", "PINU.CON"],
            "area_ha": [10.0, 4.0],
            "thlb_area_ha": [5.0, 1.0],
        }
    )
    cells = tmp_path / "cells"
    cells.mkdir()
    (cells / "cell.json").write_text(
        json.dumps(
            {
                "cell_key": "ecoprovince:Central Montane Cordillera|PICE.ENG",
                "status": "ok",
                "stand": {"distribution_evidence_tier": "direct_fit"},
                "matrix": [
                    {
                        "cell_key": "ecoprovince:Central Montane Cordillera|PICE.ENG",
                        "ecozone": "Montane Cordillera",
                        "ecoprovince": "Central Montane Cordillera",
                        "species": "PICE.ENG",
                        "product": "sawlog",
                        "sort_code": "D125L11",
                        "stream": "log",
                        "record_type": "log",
                        "volume_m3ha": 2.0,
                        "residue_volume_m3": 0.0,
                        "residue_odt": 0.0,
                    },
                    {
                        "cell_key": "ecoprovince:Central Montane Cordillera|PICE.ENG",
                        "ecozone": "Montane Cordillera",
                        "ecoprovince": "Central Montane Cordillera",
                        "species": "PICE.ENG",
                        "product": "residue",
                        "sort_code": "RESIDUE",
                        "stream": "residue",
                        "record_type": "residue",
                        "volume_m3ha": 1.0,
                        "residue_volume_m3": 1.0,
                        "residue_odt": 0.4,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "joined.csv"
    joined, summary = build_batch_join(cells, area_table, output)
    assert summary["n_matched_cells"] == 1
    assert joined.loc[joined["stream"] == "log", "volume_m3"].iloc[0] == pytest.approx(20.0)
    assert joined.loc[joined["stream"] == "residue", "residue_m3"].iloc[0] == pytest.approx(10.0)
    assert joined.loc[joined["stream"] == "residue", "residue_odt"].iloc[0] == pytest.approx(4.0)

    # A cell for a species with no area must be retained and flagged, never
    # silently assigned the 1 ha placeholder.
    area_without_species = area_table[area_table["rsyc_species"] == "PINU.CON"]
    joined_no_area, summary_no_area = build_batch_join(cells, area_without_species, output)
    assert summary_no_area["n_no_area_cells"] == 1
    assert joined_no_area["area_ha"].iloc[0] == 0.0
    assert joined_no_area["area_status"].iloc[0] == "no_area"
