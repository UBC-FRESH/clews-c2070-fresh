"""Tests for the StatCan provincial logging-industry cost proxy."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import statcan_logging_cost_proxy as model

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = (
    REPO_ROOT / "data/derived/national/harvest_basis/statcan_logging_cost_proxy"
)
FIXTURE = REPO_ROOT / "tests/fixtures/statcan_16100114/16100114_test_subset.csv"
HARVEST_CSV = (
    REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def source_row(**overrides: str) -> dict[str, str]:
    row = {
        "REF_DATE": "2024",
        "GEO": "British Columbia",
        "Principal statistics": "Total expenses",
        "North American Industry Classification System (NAICS)": (
            model.STATCAN_NAICS_INDUSTRY
        ),
        "UOM": "Dollars",
        "SCALAR_FACTOR": "thousands",
        "VALUE": "1234",
        "STATUS": "B",
        "SYMBOL": "",
    }
    return {**row, **overrides}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("statcan_logging_cost_proxy")
    summary = model.build(
        out_dir,
        source_csv=FIXTURE,
        harvest_csv=HARVEST_CSV,
        retrieved_utc="2026-11-22T15:22:42Z",
    )
    return out_dir, summary


def test_reference_year_and_fixture_parse():
    rows = model.read_statcan_rows(FIXTURE)
    index = model.build_cell_index(rows)
    assert model.select_reference_year(index) == 2024
    bc = model.extract_panel(
        index, 2024, "British Columbia", model.STATCAN_NAICS_INDUSTRY
    )
    assert bc.total_expenses.value_cad == pytest.approx(5_455_115_000.0)
    assert bc.total_expenses.status == "B"
    assert bc.energy_fuel.status == "E"


def test_unit_conversion_and_missing_statuses():
    assert model.parse_dollar_cell(source_row()).value_cad == pytest.approx(1_234_000.0)
    suppressed = model.parse_dollar_cell(source_row(STATUS="x"))
    assert suppressed.value_cad is None
    assert suppressed.status == "x"
    unreliable = model.parse_dollar_cell(source_row(STATUS="F", VALUE=""))
    assert unreliable.value_cad is None
    not_available = model.parse_dollar_cell(source_row(STATUS="..", VALUE=""))
    assert not_available.value_cad is None


def test_quality_d_is_preserved_not_treated_as_failure():
    cell = model.parse_dollar_cell(source_row(STATUS="D"))
    assert cell.value_cad == pytest.approx(1_234_000.0)
    assert cell.status == "D"


def test_unknown_status_fails_loud():
    with pytest.raises(ValueError, match="Unexpected StatCan STATUS"):
        model.parse_dollar_cell(source_row(STATUS="Z"))


def test_negative_expense_fails_loud():
    with pytest.raises(ValueError, match="Negative expense"):
        model.parse_dollar_cell(source_row(VALUE="-1"))


def test_committed_proxy_has_13_jurisdictions_and_labels():
    rows = read_csv(ARTIFACT_DIR / model.PROXY_CSV)
    assert len(rows) == 13
    assert {row["province"] for row in rows} == {spec.code for spec in model.PROVINCES}
    for row in rows:
        assert row["statcan_naics_code"] == model.NAICS_INDUSTRY_CODE
        assert row["statcan_naics_label"] == model.STATCAN_NAICS_INDUSTRY
        assert row["evidence"] == model.EVIDENCE_LABEL
        assert row["observation_type"] == model.EVIDENCE_LABEL
        assert row["not_logging_haul_breakdown"] == "true"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["fallback_used"] == "false"
        assert row["price_basis"] == "nominal_2024_CAD"


def test_expected_latest_year_values():
    rows = {row["province"]: row for row in read_csv(ARTIFACT_DIR / model.PROXY_CSV)}
    assert float(rows["BC"]["total_expenses_cad"]) == pytest.approx(5_455_115_000.0)
    assert float(rows["BC"]["industry_expense_cad_m3"]) == pytest.approx(
        140.318842, abs=1e-6
    )
    assert float(rows["QC"]["industry_expense_cad_m3"]) == pytest.approx(
        101.268489, abs=1e-6
    )
    assert float(rows["NB"]["industry_expense_cad_m3"]) == pytest.approx(
        98.666476, abs=1e-6
    )
    assert float(rows["NT"]["industry_expense_cad_m3"]) == pytest.approx(
        4.585528, abs=1e-6
    )


def test_missing_provinces_are_blank_not_zero():
    rows = {row["province"]: row for row in read_csv(ARTIFACT_DIR / model.PROXY_CSV)}
    nunavut = rows["NU"]
    assert nunavut["harvest_m3"] == "0"
    assert nunavut["industry_expense_cad_m3"] == ""
    assert nunavut["salaries_wages_share"] == ""
    assert nunavut["proxy_status"] == "undefined_zero_harvest_denominator"
    assert "harvest_denominator_zero" in nunavut["data_quality_flags"]
    assert "reported_zero_total_expenses" in nunavut["data_quality_flags"]
    assert float(nunavut["total_expenses_cad"]) == 0.0


def test_suppressed_contract_logging_is_blank_not_zero():
    rows = {row["province"]: row for row in read_csv(ARTIFACT_DIR / model.PROXY_CSV)}
    for province in ("YT", "NT", "NU"):
        row = rows[province]
        assert row["contract_logging_industry_expense_cad_m3"] == ""
        assert row["contract_logging_total_expenses_cad"] in ("", "0")
        assert row["contract_logging_proxy_status"] != "available"
    assert rows["YT"]["contract_logging_proxy_status"] == "suppressed"
    assert rows["NT"]["contract_logging_proxy_status"] == "suppressed"


def test_component_shares_sum_to_one_when_complete():
    rows = read_csv(ARTIFACT_DIR / model.PROXY_CSV)
    for row in rows:
        shares = [
            row["salaries_wages_share"],
            row["materials_supplies_share"],
            row["energy_fuel_share"],
            row["other_expense_share"],
        ]
        if any(value == "" for value in shares):
            assert row["province"] == "NU"
            continue
        assert math.isclose(
            sum(float(value) for value in shares), 1.0, rel_tol=0.0, abs_tol=2e-6
        )


def test_missing_component_is_blank_not_zero():
    rows = {row["province"]: row for row in read_csv(ARTIFACT_DIR / model.PROXY_CSV)}
    assert rows["NS"]["contract_logging_materials_supplies_cad"] == ""
    assert rows["NS"]["contract_logging_materials_supplies_share"] == ""
    assert (
        "contract_statcan_status_F_too_unreliable" in rows["NS"]["data_quality_flags"]
    )


def test_contract_logging_is_separate_from_industry_total():
    rows = {row["province"]: row for row in read_csv(ARTIFACT_DIR / model.PROXY_CSV)}
    for province, row in rows.items():
        contract = row["contract_logging_total_expenses_cad"]
        total = row["total_expenses_cad"]
        if contract == "" or total == "":
            continue
        assert float(contract) <= float(total) + 1e-6, province


def test_fixture_rebuild_matches_committed_proxy(built):
    out_dir, _ = built
    generated = read_csv(out_dir / model.PROXY_CSV)
    committed = read_csv(ARTIFACT_DIR / model.PROXY_CSV)
    assert generated == committed


def test_summary_national_reconciliation(built):
    _, summary = built
    national = summary["national"]
    assert national["total_expenses_cad"] == pytest.approx(11_695_849_000.0)
    assert national["harvest_m3"] == pytest.approx(118_977_339.0)
    assert national["industry_expense_cad_m3"] == pytest.approx(98.303165, abs=1e-6)
    assert national["canada_minus_sum_provincial_expenses_cad"] == pytest.approx(0.0)
    assert national["harvest_complete"] is True
    assert summary["coverage"]["jurisdictions_present"] == 13
    assert summary["coverage"]["proxy_available"] == 12
    assert summary["coverage"]["missing"] == ["NU"]


def test_sources_and_manifest_are_complete(built):
    out_dir, _ = built
    sources = read_csv(out_dir / model.SOURCES_CSV)
    assert {row["source_key"] for row in sources} == {
        "statcan_16100114",
        "provincial_allocation_119m",
    }
    for row in sources:
        assert row["evidence"] == model.EVIDENCE_LABEL
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    statcan = next(row for row in sources if row["source_key"] == "statcan_16100114")
    assert len(statcan["sha256_secondary"]) == 64
    assert statcan["licence"] == "Statistics Canada Open Licence"
    assert "D means acceptable" in statcan["notes"]

    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    for name, metadata in manifest["outputs"].items():
        path = out_dir / name
        assert path.is_file(), name
        assert metadata["bytes"] == path.stat().st_size
        assert len(metadata["sha256"]) == 64
    assert manifest["status"] == "industry_aggregate_proxy_not_policy_grade"
    assert "ui/public OSeCAN artifacts" in manifest["not_editing"]


def test_safe_ratio_and_status_for_zero_harvest():
    assert model.safe_ratio(1_000.0, 0.0) is None
    assert model.safe_ratio(1_000.0, None) is None
    assert model.safe_ratio(0.0, 10.0) is None
    assert model.classify_proxy_status(0.0, 0.0, None) == (
        "undefined_zero_harvest_denominator"
    )
    assert model.classify_proxy_status(None, 10.0, None) == "missing_total_expenses"
    assert model.classify_proxy_status(100.0, 10.0, 10.0) == "available"


def test_harvest_fallback_is_explicit(tmp_path: Path):
    harvest = tmp_path / "harvest.csv"
    harvest.write_text(
        "province,harvest_m3,observed_2024_m3\nBC,100,200\nAB,50,\n",
        encoding="utf-8",
    )
    records = model.read_harvest_records(harvest)
    assert records["BC"].harvest_m3 == pytest.approx(200.0)
    assert records["BC"].is_fallback is False
    assert records["AB"].harvest_m3 == pytest.approx(50.0)
    assert records["AB"].is_fallback is True
    assert records["SK"].harvest_m3 is None
