"""Contract tests for the BC log-market price surface and the market run."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from workflows.national.bucking.bc_log_market_surface import (
    MARKET_GROUPS,
    SORT_GROUP_END_USE,
    SurfaceError,
    build_sort_group_map,
    load_market_prices,
    select_latest_observations,
    write_surface,
)
from workflows.national.bucking.tsa24_sort_validation import overlap, tvd

REPO_ROOT = Path(__file__).resolve().parents[1]
PRICES = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "harvest_basis"
    / "bc_log_market"
    / "bc_log_market_prices.csv"
)
RULES = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
SURFACE_RULES = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior_log_market.yaml"
)
MARKET_RUN = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "bc_psp_validation_market_surface_v1"
)
MARKET_VALIDATION = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "tsa24_sort_validation_market_surface_v1"
)


def _price_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "report_id": "interior_1_month_not_reported_2026-09",
        "region": "interior",
        "report_basis": "1_month",
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "log_class": "not_reported",
        "species": "SPF",
        "grade": "",
        "end_use": "sawlog",
        "price_cad_m3": 100.0,
        "price_basis": "mill_delivered",
        "observation_type": "reported_market_average",
        "is_aggregate": False,
        "price_status": "reported",
        "price_plausibility": "ok",
        "volume_m3": 1000.0,
        "volume_status": "reported",
        "volume_share_pct": None,
        "volume_share_status": "",
        "report_total_volume_m3": 1000.0,
        "report_total_value_cad": 100000.0,
        "report_avg_price_cad_m3": 100.0,
        "report_totals_status": "reported",
        "source_document": "1mi_sept_26.pdf",
        "source_url": "https://example.invalid/1mi_sept_26.pdf",
        "source_sha256": "0" * 64,
        "retrieved_utc": "2026-11-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_sort_map_covers_every_pilot_sort_and_documented_group() -> None:
    sort_map = build_sort_group_map(RULES)

    assert len(sort_map) == 20
    assert sort_map["sort_code"].is_unique
    assert set(sort_map["tipsy_group"]).issubset(SORT_GROUP_END_USE)
    targets = {
        group
        for value in sort_map["target_market_groups"]
        for group in str(value).split("|")
    }
    assert targets.issubset(set(MARKET_GROUPS))

    by_code = sort_map.set_index("sort_code")
    assert by_code.loc["D125L51", "market_end_use"] == "pulpwood"
    assert by_code.loc["D125L50", "market_end_use"] == "pulpwood"
    assert by_code.loc["D305", "market_end_use"] == "sawlog"
    assert by_code.loc["D203", "market_end_use"] == "pulpwood"
    assert bool(by_code.loc["D125L51", "changes_repository_assignment"]) is True
    assert bool(by_code.loc["D305", "changes_repository_assignment"]) is True
    assert bool(by_code.loc["D125L8", "changes_repository_assignment"]) is False


def test_latest_observation_prefers_later_period_and_three_month_tie() -> None:
    frame = load_market_prices(PRICES)
    rows = [
        _price_row(period_end="2026-09-30", price_cad_m3=100.0, report_basis="1_month"),
        _price_row(
            period_end="2026-09-30",
            price_cad_m3=120.0,
            report_basis="3_month",
            report_id="interior_3_month_not_reported_2026-09",
        ),
        _price_row(
            period_end="2026-10-31",
            price_cad_m3=130.0,
            report_basis="1_month",
            report_id="interior_1_month_not_reported_2026-10",
        ),
    ]
    synthetic = pd.concat([frame, pd.DataFrame(rows)], ignore_index=True)
    selected = select_latest_observations(synthetic)
    match = selected[
        (selected["region"] == "interior")
        & (selected["species"] == "SPF")
        & (selected["end_use"] == "sawlog")
    ]
    assert len(match) == 1
    assert match.iloc[0]["price_cad_m3"] == pytest.approx(130.0)
    assert match.iloc[0]["report_basis"] == "1_month"

    same_period = synthetic[synthetic["period_end"] <= "2026-09-30"]
    selected_same = select_latest_observations(same_period)
    match_same = selected_same[
        (selected_same["region"] == "interior")
        & (selected_same["species"] == "SPF")
        & (selected_same["end_use"] == "sawlog")
    ]
    assert match_same.iloc[0]["report_basis"] == "3_month"
    assert match_same.iloc[0]["price_cad_m3"] == pytest.approx(120.0)


def test_surface_rejects_negative_and_nan_prices(tmp_path: Path) -> None:
    frame = pd.read_csv(PRICES)
    negative = frame.copy()
    negative.loc[negative.index[0], "price_cad_m3"] = -1.0
    negative_path = tmp_path / "negative.csv"
    negative.to_csv(negative_path, index=False)
    with pytest.raises(SurfaceError, match="negative or NaN"):
        load_market_prices(negative_path)

    nan = frame.copy()
    nan.loc[nan.index[0], "price_cad_m3"] = float("nan")
    nan_path = tmp_path / "nan.csv"
    nan.to_csv(nan_path, index=False)
    with pytest.raises(SurfaceError, match="negative or NaN"):
        load_market_prices(nan_path)


def test_surface_is_positive_finite_and_documented_only(tmp_path: Path) -> None:
    manifest = write_surface(
        prices_path=PRICES,
        rules_path=RULES,
        surface_rules_path=SURFACE_RULES,
        out_dir=tmp_path,
        as_of=pd.Timestamp("2026-11-23").date(),
    )
    surface = pd.read_csv(tmp_path / "price_surface.csv")
    gaps = pd.read_csv(tmp_path / "map_gaps.csv")
    coast = pd.read_csv(tmp_path / "coast_gaps.csv")
    profile = yaml.safe_load((tmp_path / "price_profile.yaml").read_text())

    assert manifest["coverage"]["n_sort_codes"] == 20
    assert manifest["coverage"]["n_tipsy_groups"] == 7
    assert manifest["coverage"]["n_surface_cells"] == 16
    assert manifest["grade_status"] == "not_assigned"
    assert set(surface["status"]).issubset(
        {"documented", "unmapped_no_published_price"}
    )
    documented = surface[surface["status"] == "documented"]
    assert not documented.empty
    assert (documented["price_cad_m3"] > 0).all()
    assert documented["price_cad_m3"].notna().all()
    assert not gaps.empty
    assert set(gaps["gap_type"]) == {"missing_published_price"}
    assert set(coast["gap_type"]) == {"unmapped_no_sort_crosswalk"}
    assert "Coast" not in profile["prices"]
    for prices in profile["prices"].values():
        for price in prices.values():
            assert float(price) > 0.0
            assert float(price) == float(price)


def test_tvd_arithmetic_and_complement_on_market_vectors() -> None:
    pilot = {"D125L63": 0.25, "D125L51": 0.15, "D20L8": 0.60}
    tipsy = {"D125L63": 0.10, "D125L51": 0.40, "D20L8": 0.50}
    assert tvd(pilot, tipsy) == pytest.approx(0.25)
    assert overlap(pilot, tipsy) == pytest.approx(0.75)
    assert tvd(pilot, tipsy) + overlap(pilot, tipsy) == pytest.approx(1.0)


@pytest.mark.skipif(
    not (MARKET_RUN / "residue.csv").exists()
    or not (MARKET_RUN / "bucked_logs.csv").exists(),
    reason="market-surface bucking artifacts are not available",
)
def test_market_run_conservation_and_no_nan() -> None:
    logs = pd.read_csv(MARKET_RUN / "bucked_logs.csv")
    residue = pd.read_csv(MARKET_RUN / "residue.csv")
    manifest = json.loads((MARKET_RUN / "manifest.json").read_text())

    assert manifest["conservation"]["pass"] is True
    assert logs["volume_m3"].notna().all()
    assert (logs["volume_m3"] >= 0).all()
    assert logs["price_cad_m3"].dropna().ge(0).all()
    assert residue["residue_volume_m3"].notna().all()
    assert (residue["residue_volume_m3"] >= 0).all()
    assert residue["taper_total_m3"].notna().all()
    assert (residue["taper_total_m3"] > 0).all()

    balance = (
        residue["recovered_log_m3"]
        + residue["residue_volume_m3"]
        - residue["taper_total_m3"]
    ).abs()
    tolerance = (1e-6 + 1e-7 * residue["taper_total_m3"]).abs()
    assert (balance <= tolerance).all()

    recovered = logs.groupby("tree_id")["volume_m3"].sum()
    ledger = residue.set_index("tree_id")["recovered_log_m3"]
    common = recovered.index.intersection(ledger.index)
    assert len(common) == len(ledger) == len(recovered)
    difference = (recovered.loc[common] - ledger.loc[common]).abs()
    assert (difference <= 1e-6).all()


@pytest.mark.skipif(
    not (MARKET_VALIDATION / "metrics.json").exists(),
    reason="market-surface sort validation artifacts are not available",
)
def test_market_validation_is_labelled_and_finite() -> None:
    metrics = json.loads((MARKET_VALIDATION / "metrics.json").read_text())
    summary = json.loads((MARKET_VALIDATION / "summary.json").read_text())
    assert metrics["label"] == "industrial_sort_validation"
    assert metrics["grade_status"] == "not_assigned"
    assert metrics["official_grade_validation"] is False
    assert summary["pass_fail"] == "not_assessed_no_pre_registered_tolerance"
    primary = metrics["primary_au_match"]
    assert primary["pooled_tvd"] >= 0.0
    assert primary["pooled_overlap"] >= 0.0
    assert primary["pooled_tvd"] + primary["pooled_overlap"] == pytest.approx(1.0)
    assert primary["volume"]["rmse_m3ha"] >= 0.0
    assert pd.notna(primary["volume"]["relative_bias"])
