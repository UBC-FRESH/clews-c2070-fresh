"""Synthetic fixtures for the forest cost/revenue ledger validator."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from workflows.national.harvest.cost_revenue import (
    LEDGER_FIELDS,
    TEMPLATE_PATH,
    Component,
    Confidence,
    Evidence,
    Jurisdiction,
    LedgerValidationError,
    Perspective,
    PricePoint,
    QuantityUnit,
    Sign,
    Unit,
    ValueBasis,
    load_ledger,
)


def base_row(**overrides: str) -> dict[str, str]:
    row = {
        "jurisdiction": "BC",
        "source": "prov_bcts_auction_results",
        "year": "2024",
        "spatial_unit": "BC-TSA24",
        "species": "SWE",
        "product": "sawlog",
        "volume_m3": "12500",
        "unit": "CAD_per_m3_log",
        "value_cad": "30.00",
        "value_basis": "observed_sale:standing",
        "perspective": "crown",
        "component": "stumpage_base",
        "sign": "1",
        "confidence": "medium",
        "licence": "BCTS-24001",
        "notes": (
            "currency_basis=CAD_nominal; source_table=bcts_sale_results; "
            "base=upset"
        ),
    }
    row.update(overrides)
    return row


def mirror_row(**overrides: str) -> dict[str, str]:
    row = base_row(
        perspective="forest_sector",
        sign="-1",
        notes=(
            "currency_basis=CAD_nominal; source_table=bcts_sale_results; "
            "mirror=1"
        ),
    )
    row.update(overrides)
    return row


def premium_row(**overrides: str) -> dict[str, str]:
    row = base_row(
        component="stumpage_bid_premium",
        value_cad="12.50",
        notes=(
            "currency_basis=CAD_nominal; source_table=bcts_sale_results; "
            "premium=winning minus upset"
        ),
    )
    row.update(overrides)
    return row


def delivered_row(**overrides: str) -> dict[str, str]:
    row = base_row(
        source="prov_bc_log_price_deck",
        value_cad="98.40",
        value_basis="published_average:mill_gate",
        perspective="forest_sector",
        component="delivered_log_revenue",
        sign="1",
        notes="currency_basis=CAD_nominal; source_table=log_price_deck; deck=1",
    )
    row.update(overrides)
    return row


def harvest_row(**overrides: str) -> dict[str, str]:
    row = base_row(
        source="prov_bc_appraisal_manual",
        value_cad="38.10",
        value_basis="appraisal_allowance:roadside",
        perspective="forest_sector",
        component="harvest",
        sign="-1",
        notes=(
            "currency_basis=CAD_nominal; source_table=appraisal_manual; "
            "allowance=not actual"
        ),
    )
    row.update(overrides)
    return row


def valid_rows() -> list[dict[str, str]]:
    return [
        base_row(),
        mirror_row(),
        premium_row(),
        premium_row(
            perspective="forest_sector",
            sign="-1",
            notes="currency_basis=CAD_nominal; mirror=1",
        ),
        harvest_row(),
        delivered_row(),
    ]


def write_ledger(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "forest_cost_revenue.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_valid_fixture_parses_and_pairs_mirrors(tmp_path: Path) -> None:
    ledger = load_ledger(write_ledger(tmp_path, valid_rows()))

    assert len(ledger) == 6
    crown = ledger.rows[0]
    assert crown.jurisdiction is Jurisdiction.BC
    assert crown.perspective is Perspective.CROWN
    assert crown.component is Component.STUMPAGE_BASE
    assert crown.sign is Sign.POSITIVE
    assert crown.unit is Unit.CAD_PER_M3_LOG
    assert crown.unit.quantity_unit is QuantityUnit.M3_LOG
    assert crown.value_basis == ValueBasis(
        Evidence.OBSERVED_SALE, PricePoint.STANDING
    )
    assert ledger.rows[1].mirror_requested
    assert ledger.rows[1].perspective is Perspective.FOREST_SECTOR
    assert ledger.rows[1].sign is Sign.NEGATIVE
    assert ledger.rows[4].confidence is Confidence.MEDIUM


def test_template_matches_the_16_field_schema() -> None:
    with TEMPLATE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert tuple(header) == LEDGER_FIELDS
        assert list(reader) == []
    assert load_ledger(TEMPLATE_PATH).is_empty


def test_bad_sign_is_rejected(tmp_path: Path) -> None:
    path = write_ledger(tmp_path, [base_row(sign="-1")])
    with pytest.raises(LedgerValidationError, match="sign"):
        load_ledger(path)


def test_duplicate_key_is_rejected(tmp_path: Path) -> None:
    path = write_ledger(tmp_path, [base_row(), base_row()])
    with pytest.raises(LedgerValidationError, match="duplicate 9-field key"):
        load_ledger(path)


def test_missing_volume_is_rejected(tmp_path: Path) -> None:
    path = write_ledger(tmp_path, [base_row(volume_m3="")])
    with pytest.raises(LedgerValidationError, match="volume_m3"):
        load_ledger(path)


def test_non_finite_and_negative_values_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LedgerValidationError, match="finite"):
        load_ledger(write_ledger(tmp_path, [base_row(volume_m3="NaN")]))
    with pytest.raises(LedgerValidationError, match=">= 0"):
        load_ledger(write_ledger(tmp_path, [base_row(value_cad="-1")]))


def test_mirror_on_non_transfer_is_rejected(tmp_path: Path) -> None:
    row = delivered_row(notes="currency_basis=CAD_nominal; mirror=1")
    with pytest.raises(LedgerValidationError, match="genuine transfers"):
        load_ledger(write_ledger(tmp_path, [row]))


def test_unpaired_and_unequal_mirrors_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LedgerValidationError, match="no matching"):
        load_ledger(write_ledger(tmp_path, [mirror_row()]))
    with pytest.raises(LedgerValidationError, match="no matching"):
        load_ledger(
            write_ledger(
                tmp_path,
                [base_row(), mirror_row(value_cad="30.01")],
            )
        )


def test_unknown_unit_and_perspective_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LedgerValidationError, match="unit"):
        load_ledger(write_ledger(tmp_path, [base_row(unit="CAD_per_kg")]))
    with pytest.raises(LedgerValidationError, match="perspective"):
        load_ledger(write_ledger(tmp_path, [base_row(perspective="society")]))


def test_bcts_and_hbs_cannot_both_be_additive(tmp_path: Path) -> None:
    hbs = base_row(source="bc_hbs", value_cad="30.50")
    with pytest.raises(LedgerValidationError, match="BCTS auction results and BC HBS"):
        load_ledger(write_ledger(tmp_path, [base_row(), hbs]))


def test_bmmb_and_tarification_cannot_both_be_additive(tmp_path: Path) -> None:
    bmmb = base_row(
        jurisdiction="QC",
        source="qc_bmmb_sales",
        spatial_unit="QC-UA-0642",
        species="SAB",
        value_basis="observed_sale:standing",
        confidence="medium",
        licence="LOT-2024-119",
        notes="currency_basis=CAD_nominal; source_table=bmmb_sales",
    )
    tarif = base_row(
        jurisdiction="QC",
        source="qc_tarification",
        spatial_unit="QC-UA-0642",
        species="SAB",
        value_basis="administered_rate:standing",
        confidence="high",
        licence="LOT-2024-119",
        notes="currency_basis=CAD_nominal; source_table=tarification",
    )
    with pytest.raises(LedgerValidationError, match="BMMB sales and QC tarification"):
        load_ledger(write_ledger(tmp_path, [bmmb, tarif]))


def test_comparison_only_source_is_allowed(tmp_path: Path) -> None:
    comparison = base_row(
        source="bc_hbs",
        value_cad="30.50",
        notes=(
            "currency_basis=CAD_nominal; comparison_only=1; "
            "not_additive_with=prov_bcts_auction_results"
        ),
    )
    ledger = load_ledger(write_ledger(tmp_path, [base_row(), comparison]))
    assert len(ledger) == 2


def test_stumpage_total_is_exclusive_with_base(tmp_path: Path) -> None:
    total = base_row(
        component="stumpage_total",
        value_cad="42.50",
        notes="currency_basis=CAD_nominal; source_table=billing",
    )
    with pytest.raises(LedgerValidationError, match="stumpage_total"):
        load_ledger(write_ledger(tmp_path, [base_row(), total]))


def test_currency_and_coverage_tokens_are_required(tmp_path: Path) -> None:
    with pytest.raises(LedgerValidationError, match="currency_basis"):
        load_ledger(
            write_ledger(tmp_path, [base_row(notes="source_table=bcts")])
        )
    with pytest.raises(LedgerValidationError, match="coverage_denominator"):
        load_ledger(
            write_ledger(
                tmp_path,
                [
                    base_row(
                        notes=(
                            "currency_basis=CAD_nominal; coverage=partial; "
                            "source_table=bcts"
                        )
                    )
                ],
            )
        )


def test_apparent_volume_requires_a_registered_conversion(tmp_path: Path) -> None:
    path = write_ledger(
        tmp_path,
        [
            base_row(
                notes=(
                    "currency_basis=CAD_nominal; volume_basis=m3_apparent; "
                    "source_table=bcts"
                )
            )
        ],
    )
    with pytest.raises(LedgerValidationError, match="conversion"):
        load_ledger(path)


def test_standing_bid_cannot_be_a_delivered_log_price(tmp_path: Path) -> None:
    row = delivered_row(value_basis="observed_sale:standing")
    with pytest.raises(LedgerValidationError, match="standing bid is not a log price"):
        load_ledger(write_ledger(tmp_path, [row]))


def test_provisional_source_cannot_claim_high_confidence(tmp_path: Path) -> None:
    path = write_ledger(tmp_path, [base_row(confidence="high")])
    with pytest.raises(LedgerValidationError, match="provisional source"):
        load_ledger(path)
