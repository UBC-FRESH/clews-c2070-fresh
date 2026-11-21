"""Tests for the BC BCTS + QC MERIS cost/revenue merge and coverage report."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.harvest import merge_cost_revenue as merge
from workflows.national.harvest.cost_revenue import (
    CORE_LEDGER_FIELDS,
    Component,
    Evidence,
    LedgerValidationError,
    PricePoint,
    load_ledger,
)

BC_BLOCK = merge.BC_BLOCK_PATH
QC_BLOCK = merge.QC_BLOCK_PATH


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture()
def merged(tmp_path: Path) -> merge.MergeResult:
    return merge.run_merge(output_dir=tmp_path)


def test_merge_conserves_every_input_row(merged: merge.MergeResult) -> None:
    input_rows = read_csv(BC_BLOCK) + read_csv(QC_BLOCK)
    output_rows = read_csv(merged.output_path)

    output_core = [
        {name: row[name] for name in CORE_LEDGER_FIELDS} for row in output_rows
    ]
    assert output_core == input_rows
    assert all(row["quality"] == "NA" for row in output_rows)
    assert all(row["dbh_cm"] == "" for row in output_rows)
    assert len(merged.ledger) == len(input_rows) == 940
    assert merged.summary["row_counts"]["by_jurisdiction"] == {"BC": 200, "QC": 740}
    assert merged.summary["row_counts"]["by_source"] == {
        "prov_bcts_auction_results": 200,
        "prov_qc_meris": 740,
    }
    assert merged.summary["row_counts"]["by_perspective"] == {
        "crown": 100,
        "forest_sector": 840,
    }
    assert merged.summary["row_counts"]["by_evidence_class"] == {
        "model_input": 740,
        "observed": 200,
    }
    assert merged.summary["merge_rules"]["no_imputation"] is True


def test_merged_ledger_has_unique_keys_and_one_additive_source(
    merged: merge.MergeResult,
) -> None:
    ledger = load_ledger(merged.output_path)
    keys = [row.key for row in ledger]
    assert len(keys) == len(set(keys))

    sources_by_jurisdiction: dict[str, set[str]] = {}
    additive_sources: dict[tuple, set[str]] = {}
    for row in ledger:
        sources_by_jurisdiction.setdefault(row.jurisdiction.value, set()).add(
            row.source
        )
        if not row.is_comparison_only:
            additive_sources.setdefault(row.coverage_key, set()).add(row.source)

    assert sources_by_jurisdiction == {
        "BC": {"prov_bcts_auction_results"},
        "QC": {"prov_qc_meris"},
    }
    assert all(len(sources) == 1 for sources in additive_sources.values())


def test_a_second_additive_source_fails_closed(tmp_path: Path) -> None:
    qc_rows = read_csv(QC_BLOCK)
    duplicate = dict(qc_rows[0])
    duplicate["source"] = "prov_qc_tarification"
    tampered = tmp_path / "tampered_qc.csv"
    with tampered.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=merge.LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(qc_rows + [duplicate])

    with pytest.raises(LedgerValidationError, match="source exclusivity"):
        merge.run_merge(
            output_dir=tmp_path / "out",
            block_specs=((BC_BLOCK, "BC"), (tampered, "QC")),
        )
    assert not (tmp_path / "out" / "forest_cost_revenue.csv").exists()


def test_unpaired_mirror_fails_closed(tmp_path: Path) -> None:
    bc_rows = read_csv(BC_BLOCK)
    mirror = next(row for row in bc_rows if row["notes"].endswith("mirror=1"))
    counterpart_index = next(
        index
        for index, row in enumerate(bc_rows)
        if row["licence"] == mirror["licence"]
        and row["component"] == mirror["component"]
        and row["perspective"] == "crown"
        and not row["notes"].endswith("mirror=1")
    )
    tampered = tmp_path / "unpaired_bc.csv"
    with tampered.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=merge.LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(
            bc_rows[:counterpart_index] + bc_rows[counterpart_index + 1 :]
        )

    with pytest.raises(LedgerValidationError, match="mirror row has no matching"):
        merge.run_merge(
            output_dir=tmp_path / "out",
            block_specs=((tampered, "BC"), (QC_BLOCK, "QC")),
        )


def test_coverage_math_uses_stated_denominators(merged: merge.MergeResult) -> None:
    coverage = merged.summary["coverage"]
    bc = coverage["bc_bcts"]
    qc = coverage["qc_meris"]

    assert bc["ledger"]["unique_sale_volume_m3"] == pytest.approx(1_313_211)
    assert bc["ledger"]["awarded_tsls"] == 51
    assert bc["ledger"]["awarded_blocks"] == 84
    assert bc["sample_frame"]["scheduled_volume_m3"] == pytest.approx(1_940_031.2286)
    assert bc["awarded_share_of_scheduled_volume"]["share"] == pytest.approx(
        1_313_211 / 1_940_031.2286, abs=1e-6
    )
    assert bc["awarded_volume_share_of_bc_2024_harvest"][
        "share"
    ] == pytest.approx(1_313_211 / 38_876_568, abs=1e-6)
    assert bc["scheduled_volume_share_of_bc_2024_harvest"][
        "share"
    ] == pytest.approx(1_940_031.2286 / 38_876_568, abs=1e-6)

    assert qc["ledger"]["current_stumpage_rate_rows"] == 0
    assert qc["current_rate_row_share"]["share"] == pytest.approx(0.0)
    assert qc["zone_share"]["share"] == pytest.approx(191 / 191, abs=1e-6)
    assert qc["treatment_share"]["share"] == pytest.approx(40 / 94, abs=1e-6)
    assert qc["ledger"]["observed_volume_m3"] == 0.0
    assert qc["ledger"]["observed_rows"] == 0
    assert qc["ledger"]["model_input_rows"] == 740
    assert qc["ledger"]["delivered_log_revenue_model_input_rows"] == 127
    assert qc["ledger"]["delivered_log_revenue_observed_rows"] == 0
    assert qc["harvest_volume_share"]["share"] is None
    assert qc["harvest_volume_share"]["status"] == "not_estimable"

    assert coverage["denominators"]["bc_plus_qc_share_of_national"][
        "share"
    ] == pytest.approx(64_815_624 / 118_977_339, abs=1e-6)
    assert merged.summary["row_counts"]["total_rows"] == 940
    assert merged.summary["evidence_classes"]["observed_rows"] == 200
    assert merged.summary["evidence_classes"]["model_input_rows"] == 740


def test_no_cross_perspective_or_cross_province_summation(
    merged: merge.MergeResult,
) -> None:
    totals = merged.summary["value_totals_cad"]
    assert totals
    assert {entry["perspective"] for entry in totals} <= {
        "crown",
        "forest_sector",
    }
    assert all("total" not in entry["perspective"] for entry in totals)
    assert {entry["jurisdiction"] for entry in totals} == {"BC", "QC"}

    # A transfer is +Crown and -sector by exactly the same absolute value.
    bc_crown = sum(
        entry["value_cad"]
        for entry in totals
        if entry["jurisdiction"] == "BC" and entry["perspective"] == "crown"
    )
    bc_sector = sum(
        entry["value_cad"]
        for entry in totals
        if entry["jurisdiction"] == "BC"
        and entry["perspective"] == "forest_sector"
    )
    assert bc_crown == pytest.approx(-bc_sector)

    assert all(
        entry["jurisdiction"] not in {"CAN", "BC+QC"}
        for entry in totals
    )
    assert all(
        entry["value_cad"] is None
        for entry in totals
        if entry["evidence_class"] == "model_input"
    )
    assert "total" not in merged.summary["coverage"]


def test_no_observed_delivered_log_revenue_was_fabricated(
    merged: merge.MergeResult,
) -> None:
    delivered = [
        row
        for row in merged.ledger
        if row.component is Component.DELIVERED_LOG_REVENUE
    ]
    assert len(delivered) == 127
    assert {row.value_basis.evidence for row in delivered} == {Evidence.MODEL_INPUT}
    assert all(row.notes_tokens["not_observed"] == "1" for row in delivered)

    bcts = [row for row in merged.ledger if row.source == "prov_bcts_auction_results"]
    qc = [row for row in merged.ledger if row.source == "prov_qc_meris"]
    assert {row.value_basis.evidence for row in bcts} == {Evidence.OBSERVED_SALE}
    assert {row.value_basis.price_point for row in bcts} == {PricePoint.STANDING}
    assert {row.value_basis.evidence for row in qc} == {Evidence.MODEL_INPUT}
    assert all(
        row.component is not Component.STUMPAGE_TOTAL for row in merged.ledger
    )


def test_coverage_artifacts_are_written_and_self_describing(
    merged: merge.MergeResult,
) -> None:
    summary = json.loads(merged.coverage_json_path.read_text(encoding="utf-8"))
    assert summary["status"] == "screening_only"
    assert summary["policy_grade"] is False
    assert summary["coverage"]["bc_bcts"]["sample_not_population"] is True
    assert summary["coverage"]["qc_meris"]["sample_not_population"] is True

    markdown = merged.coverage_md_path.read_text(encoding="utf-8")
    assert "not a population" in markdown
    assert "model-input" in markdown
    assert "not estimable" in markdown
    assert "`not_estimated`" in markdown
    assert "delivered_log_revenue" in markdown
    assert "stumpage_total" in markdown
