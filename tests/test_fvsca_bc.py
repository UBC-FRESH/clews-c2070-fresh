"""Focused regression tests for the BC FVS-CA adapter.

The tests use synthetic DataFrames that satisfy the harmonized v0 column
contract.  They never read the archived raw corpora, so they run quickly and
isolate the adapter's key, filter, and referential-integrity rules.
"""

from __future__ import annotations

import pandas as pd
import pytest

from workflows.national.fvsca.bc import (
    BC_INTERVAL_COLUMNS,
    BC_TREE_SURVIVAL_COLUMNS,
    BC_VISIT_COLUMNS,
    build_bc_tables,
    classify_species,
)
from workflows.national.fvsca.profiles import (
    BC_PROFILE,
    BC_SUPPORTED_ZONES,
    derive_bec,
)


class _SyntheticSource:
    """A minimal in-memory PlotSource for adapter tests."""

    source_labels = ("faib_psp",)

    def __init__(
        self,
        sites: pd.DataFrame,
        visits: pd.DataFrame,
        trees: pd.DataFrame,
    ) -> None:
        self._sites = sites
        self._visits = visits
        self._trees = trees

    def sites(self) -> pd.DataFrame:
        return self._sites

    def visits(self) -> pd.DataFrame:
        return self._visits

    def trees(self, columns: object = None) -> pd.DataFrame:
        return self._trees

    def intervals(self) -> pd.DataFrame:
        return pd.DataFrame()


def _sites() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source": ["faib_psp"],
            "site_id": ["S1"],
            "eco_zone": ["CWH"],
            "eco_subzone": ["vm"],
            "eco_variant": ["1"],
            "plot_design": ["PSP_G"],
        }
    )


def _visits() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source": ["faib_psp", "faib_psp"],
            "site_id": ["S1", "S1"],
            "visit_id": ["V1", "V2"],
            "year": [2000, 2010],
            "n_trees": [4, 4],
            "ba_m2ha": [10.0, 12.0],
            "tph": [400.0, 400.0],
            "qmd_cm": [18.0, 20.0],
            "top_height_m": [20.0, 22.0],
            "volume_m3ha": [100.0, 120.0],
            "site_index": [25.0, 25.0],
            "age": [50.0, 60.0],
        }
    )


def _tree_rows(
    *,
    status_by_visit: dict[str, str] | None = None,
    dbh_by_visit: dict[str, float | None] | None = None,
) -> pd.DataFrame:
    """Two plots x two visits x two trees, all live by default."""
    status_by_visit = status_by_visit or {}
    dbh_by_visit = dbh_by_visit or {}
    rows: list[dict[str, object]] = []
    for plot in (1, 2):
        for visit_id in ("V1", "V2"):
            for tree_no, dbh in ((1, 10.0), (2, 20.0)):
                rows.append(
                    {
                        "source": "faib_psp",
                        "site_id": "S1",
                        "visit_id": visit_id,
                        "tree_id": f"{plot}-{tree_no}",
                        "species_code": "HW" if tree_no == 1 else "SS",
                        "species_group": "conifer",
                        "dbh_cm": dbh_by_visit.get(visit_id, dbh),
                        "height_m": 15.0,
                        "status": status_by_visit.get(visit_id, "live"),
                        "crown_class": "C",
                        "expansion_factor": 100.0,
                        "damage_code": None,
                    }
                )
    return pd.DataFrame(rows)


def _tables(
    *,
    trees: pd.DataFrame | None = None,
    tsa_lookup: dict[str, str] | None = None,
):
    return build_bc_tables(
        _SyntheticSource(
            _sites(),
            _visits(),
            trees if trees is not None else _tree_rows(),
        ),
        tsa_lookup=tsa_lookup,
    )


def test_bc_profile_bec_site_key_and_supported_zones() -> None:
    assert derive_bec("cwh", "VM", 1.0) == "CWHvm1"
    assert derive_bec(None, "vm", "1") is None
    assert derive_bec("SBS", None, None) == "SBS"
    assert BC_PROFILE.site_key(
        {"eco_zone": "cwh", "eco_subzone": "vm", "eco_variant": "1"}
    ) == "CWHvm1"
    assert BC_PROFILE.accepts_bec("CWHvm1")
    assert BC_PROFILE.accepts_zone("sbs")
    assert not BC_PROFILE.accepts_bec("SWBun")
    assert tuple(BC_PROFILE.supported_zones) == BC_SUPPORTED_ZONES
    assert len(BC_SUPPORTED_ZONES) == 12


def test_species_classification_direct_donor_and_unmapped() -> None:
    direct = classify_species("HW")
    assert direct.fvs_jsp_code == "HW"
    assert direct.group == "HW"
    assert direct.resolution == "direct"

    donor = classify_species("SS")
    assert donor.fvs_jsp_code == "SE"
    assert donor.group == "SE"
    assert donor.resolution == "donor"

    unmapped = classify_species("DG")
    assert unmapped.fvs_jsp_code is None
    assert unmapped.group == "UNKNOWN"
    assert unmapped.resolution == "unmapped"

    # SW is a calibration group but not a JSP code or donor alias.
    sw = classify_species("SW")
    assert sw.fvs_jsp_code is None
    assert sw.group == "SE"
    assert sw.resolution == "unmapped"


def test_consumer_schema_columns_are_declared_and_unique() -> None:
    tables = _tables()
    for frame, required in (
        (tables.visits, BC_VISIT_COLUMNS),
        (tables.intervals, BC_INTERVAL_COLUMNS),
        (tables.tree_survival, BC_TREE_SURVIVAL_COLUMNS),
    ):
        assert len(frame.columns) == len(set(frame.columns))
        assert list(frame.columns[: len(required)]) == list(required)
        assert set(required) <= set(frame.columns)


def test_two_plots_two_visits_stay_per_plot() -> None:
    tables = _tables()
    assert len(tables.visits) == 4
    assert len(tables.intervals) == 2
    assert len(tables.tree_survival) == 4

    assert tables.intervals["interval_id"].is_unique
    assert set(tables.intervals["PLOT"]) == {1, 2}
    assert set(tables.tree_survival["PLOT"]) == {1, 2}

    plot_by_interval = tables.intervals.set_index("interval_id")["PLOT"]
    for interval_id, group in tables.tree_survival.groupby("interval_id"):
        assert group["PLOT"].nunique() == 1
        assert int(group["PLOT"].iloc[0]) == int(plot_by_interval.loc[interval_id])
        assert len(group) == 2
        assert set(group["TREE_NO"]) == {1, 2}


def test_dead_endpoint_with_null_dbh_stays_a_known_death() -> None:
    trees = _tree_rows()
    # Tree 2 on plot 1 is dead at V2 with no DBH and no expansion factor.
    mask = (
        trees["visit_id"].eq("V2")
        & trees["tree_id"].eq("1-2")
    )
    trees.loc[mask, "status"] = "dead"
    trees.loc[mask, "dbh_cm"] = None
    trees.loc[mask, "height_m"] = None
    trees.loc[mask, "expansion_factor"] = None

    tables = _tables(trees=trees)
    interval_id = "S1|1|2000|2010"
    endpoint = tables.tree_survival.loc[
        tables.tree_survival["interval_id"].eq(interval_id)
        & tables.tree_survival["TREE_NO"].eq(2)
    ]
    assert len(endpoint) == 1
    row = endpoint.iloc[0]
    assert row["status"] == "dead"
    assert int(row["dead"]) == 1
    assert int(row["known"]) == 1
    assert row["species"] == "SE"
    assert pd.isna(row["dbh_end"])

    interval = tables.intervals.set_index("interval_id").loc[interval_id]
    assert int(interval["n_dead_trees"]) == 1
    assert int(interval["n_missing_trees"]) == 0
    assert float(interval["dead_ba"]) > 0


def test_referential_integrity_between_consumer_tables() -> None:
    tables = _tables()
    interval_ids = set(tables.intervals["interval_id"])
    assert set(tables.tree_survival["interval_id"]) <= interval_ids

    visit_ids = set(tables.visits["CLSTR_ID"])
    for row in tables.intervals[["visit_id0", "visit_id1"]].itertuples(index=False):
        assert row.visit_id0 in visit_ids
        assert row.visit_id1 in visit_ids

    assert tables.intervals[["visit_id0", "visit_id1"]].notna().all().all()
    assert tables.tree_survival["interval_id"].notna().all()
    assert tables.tree_survival["CLSTR_ID"].isin(visit_ids).all()


def test_multi_plot_visit_volume_is_null_with_a_basis() -> None:
    tables = _tables()
    assert tables.visits["volume"].isna().all()
    assert set(tables.visits["volume_basis"]) == {
        "cluster_multi_plot_not_apportioned"
    }
    assert any("more than one plot" in warning for warning in tables.warnings)
    assert tables.stats["multi_plot_visit_units"] == 2
    assert tables.stats["volume_null_multi_plot_visit_units"] == 2
