"""Synthetic fixtures for the forest cost/revenue ledger validator."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from workflows.national.harvest.cost_revenue import (
    CORE_LEDGER_FIELDS,
    LEDGER_FIELDS,
    OPTIONAL_LEDGER_FIELDS,
    TEMPLATE_PATH,
    Component,
    Confidence,
    Evidence,
    Jurisdiction,
    LedgerValidationError,
    Perspective,
    PricePoint,
    Quality,
    QuantityUnit,
    Sign,
    Unit,
    ValueBasis,
    load_ledger,
)

LEGACY_LEDGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/derived/national/harvest_basis/forest_cost_revenue"
    / "forest_cost_revenue.csv"
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


def model_input_row(**overrides: str) -> dict[str, str]:
    row = base_row(
        source="prov_qc_meris",
        jurisdiction="QC",
        spatial_unit="QC-MERIS-PROV",
        species="BOJ",
        product="sawlog",
        value_cad="202.9370",
        value_basis="model_input:mill_gate",
        perspective="forest_sector",
        component="delivered_log_revenue",
        sign="1",
        confidence="medium",
        licence="QC-MERIS-BOJ-F1-C",
        notes=(
            "currency_basis=CAD_nominal; source_table=tbl_Essences_frm_inv_tiges; "
            "not_observed=1; model_input=1; price_point=mill_gate"
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


def write_legacy_ledger(
    tmp_path: Path, rows: list[dict[str, str]], *, name: str = "legacy.csv"
) -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CORE_LEDGER_FIELDS)
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


def test_template_matches_the_18_field_schema() -> None:
    with TEMPLATE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert tuple(header) == LEDGER_FIELDS
        assert tuple(header[-2:]) == OPTIONAL_LEDGER_FIELDS
        assert list(reader) == []
    assert load_ledger(TEMPLATE_PATH).is_empty


def test_bad_sign_is_rejected(tmp_path: Path) -> None:
    path = write_ledger(tmp_path, [base_row(sign="-1")])
    with pytest.raises(LedgerValidationError, match="sign"):
        load_ledger(path)


def test_duplicate_key_is_rejected(tmp_path: Path) -> None:
    path = write_ledger(tmp_path, [base_row(), base_row()])
    with pytest.raises(LedgerValidationError, match="duplicate ledger key"):
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


def test_model_input_evidence_parses_with_flags(tmp_path: Path) -> None:
    ledger = load_ledger(write_ledger(tmp_path, [model_input_row()]))

    assert len(ledger) == 1
    row = ledger.rows[0]
    assert row.value_basis == ValueBasis(
        Evidence.MODEL_INPUT, PricePoint.MILL_GATE
    )
    assert row.notes_tokens["model_input"] == "1"
    assert row.notes_tokens["not_observed"] == "1"
    assert row.confidence is Confidence.MEDIUM


def test_model_input_evidence_requires_not_observed_and_model_input(
    tmp_path: Path,
) -> None:
    with pytest.raises(LedgerValidationError, match="model_input=1"):
        load_ledger(
            write_ledger(
                tmp_path,
                [
                    model_input_row(
                        notes=(
                            "currency_basis=CAD_nominal; "
                            "source_table=tbl_Essences_frm_inv_tiges; "
                            "not_observed=1"
                        )
                    )
                ],
            )
        )
    with pytest.raises(LedgerValidationError, match="not_observed=1"):
        load_ledger(
            write_ledger(
                tmp_path,
                [
                    model_input_row(
                        notes=(
                            "currency_basis=CAD_nominal; "
                            "source_table=tbl_Essences_frm_inv_tiges; "
                            "model_input=1"
                        )
                    )
                ],
            )
        )


def test_model_input_evidence_cannot_claim_high_confidence(
    tmp_path: Path,
) -> None:
    path = write_ledger(
        tmp_path,
        [
            model_input_row(
                source="qc_meris_model_input",
                confidence="high",
            )
        ],
    )
    with pytest.raises(LedgerValidationError, match="model_input evidence"):
        load_ledger(path)


def test_observed_evidence_rejects_model_input_token(tmp_path: Path) -> None:
    row = base_row(
        notes=(
            "currency_basis=CAD_nominal; source_table=bcts_sale_results; "
            "base=upset; model_input=1"
        )
    )
    with pytest.raises(LedgerValidationError, match="model_input=1 token requires"):
        load_ledger(write_ledger(tmp_path, [row]))


def test_observed_evidence_rejects_not_observed_token(tmp_path: Path) -> None:
    row = base_row(
        notes=(
            "currency_basis=CAD_nominal; source_table=bcts_sale_results; "
            "base=upset; not_observed=1"
        )
    )
    with pytest.raises(LedgerValidationError, match="not_observed=1 token conflicts"):
        load_ledger(write_ledger(tmp_path, [row]))


def test_quality_distinguishes_previously_colliding_keys(tmp_path: Path) -> None:
    rows = [
        base_row(quality="F1"),
        base_row(quality="F2"),
        base_row(quality="F4"),
    ]
    ledger = load_ledger(write_ledger(tmp_path, rows))

    assert len(ledger) == 3
    assert {row.quality for row in ledger} == {
        Quality.F1,
        Quality.F2,
        Quality.F4,
    }
    assert len({row.key for row in ledger}) == 3


def test_duplicate_key_with_same_quality_and_dbh_is_rejected(
    tmp_path: Path,
) -> None:
    row = model_input_row(quality="NA", dbh_cm="20")
    with pytest.raises(LedgerValidationError, match="duplicate ledger key"):
        load_ledger(write_ledger(tmp_path, [row, dict(row)]))


def test_quality_is_valid_for_log_value_rows(tmp_path: Path) -> None:
    ledger = load_ledger(
        write_ledger(
            tmp_path,
            [
                base_row(quality="F1"),
                delivered_row(
                    jurisdiction="QC",
                    source="prov_qc_meris",
                    spatial_unit="QC-MERIS-PROV",
                    species="BOJ",
                    quality="F1",
                    value_basis="model_input:mill_gate",
                    confidence="medium",
                    licence="QC-MERIS-BOJ-F1-C",
                    notes=(
                        "currency_basis=CAD_nominal; "
                        "source_table=tbl_Essences_frm_inv_tiges; "
                        "not_observed=1; model_input=1"
                    ),
                ),
                base_row(
                    jurisdiction="QC",
                    source="prov_qc_meris",
                    product="mixed_log",
                    quality="AUTRE",
                    value_basis="model_input:standing",
                    confidence="medium",
                    licence="QC-MERIS-BOJ-AUTRE-C",
                    notes=(
                        "currency_basis=CAD_nominal; "
                        "source_table=tbl_redevances_F1234; "
                        "not_observed=1; model_input=1"
                    ),
                ),
            ],
        )
    )

    assert {row.quality for row in ledger} == {
        Quality.F1,
        Quality.AUTRE,
    }


def test_quality_requires_a_compatible_product_and_component(
    tmp_path: Path,
) -> None:
    with pytest.raises(LedgerValidationError, match="requires product sawlog"):
        load_ledger(
            write_ledger(tmp_path, [base_row(quality="F1", product="pulpwood")])
        )
    with pytest.raises(LedgerValidationError, match="log value components"):
        load_ledger(
            write_ledger(
                tmp_path,
                [harvest_row(quality="F1", product="sawlog")],
            )
        )
    with pytest.raises(LedgerValidationError, match="requires product peeler"):
        load_ledger(
            write_ledger(tmp_path, [base_row(quality="DER", product="sawlog")])
        )


def test_quality_requires_a_log_unit(tmp_path: Path) -> None:
    row = base_row(
        product="harvest_residue",
        component="residue_recovery",
        sign="-1",
        perspective="forest_sector",
        value_basis="model_derived:facility_gate",
        unit="CAD_per_odt_residue",
        quality="F1",
        notes="currency_basis=CAD_nominal; source_table=residue_ledger",
    )
    with pytest.raises(LedgerValidationError, match="CAD_per_m3_log"):
        load_ledger(write_ledger(tmp_path, [row]))


def test_dbh_is_valid_only_for_model_input_revenue_curves(
    tmp_path: Path,
) -> None:
    ledger = load_ledger(write_ledger(tmp_path, [model_input_row(dbh_cm="20")]))
    assert ledger.rows[0].dbh_cm == 20.0

    with pytest.raises(LedgerValidationError, match="model-input species/DBH"):
        load_ledger(write_ledger(tmp_path, [base_row(dbh_cm="20")]))
    with pytest.raises(LedgerValidationError, match="delivered_log_revenue curve"):
        load_ledger(
            write_ledger(
                tmp_path,
                [
                    model_input_row(
                        component="harvest",
                        sign="-1",
                        dbh_cm="20",
                    )
                ],
            )
        )
    with pytest.raises(LedgerValidationError, match="cannot also carry a quality"):
        load_ledger(
            write_ledger(
                tmp_path,
                [model_input_row(quality="F1", dbh_cm="20")],
            )
        )
    with pytest.raises(LedgerValidationError, match="must be > 0"):
        load_ledger(write_ledger(tmp_path, [model_input_row(dbh_cm="0")]))
    with pytest.raises(LedgerValidationError, match="must be finite"):
        load_ledger(
            write_ledger(tmp_path, [model_input_row(dbh_cm="NaN")])
        )


def test_legacy_16_field_header_loads_with_na_quality_and_no_dbh(
    tmp_path: Path,
) -> None:
    ledger = load_ledger(write_legacy_ledger(tmp_path, valid_rows()))

    assert len(ledger) == len(valid_rows())
    assert all(row.quality is Quality.NA for row in ledger)
    assert all(row.dbh_cm is None for row in ledger)


def test_merged_ledger_loads_with_quality_and_dbh_dimensions() -> None:
    if not LEGACY_LEDGER_PATH.exists():
        pytest.skip("merged ledger is not materialized")
    ledger = load_ledger(LEGACY_LEDGER_PATH)

    assert len(ledger) == 2_888
    assert sum(1 for row in ledger if row.quality is not Quality.NA) == 251
    assert sum(1 for row in ledger if row.dbh_cm is not None) == 1_824
    assert all(
        row.quality is Quality.NA
        for row in ledger
        if row.dbh_cm is not None
    )
