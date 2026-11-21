"""Focused unit tests for the MERIS QC extractor.

These tests use small synthetic source rows so they do not require the ACCDB.
They exercise the price-deck statuses, curve validity guards, the
single-profile equivalence guard, the PATE/AUTRE product mapping, placeholder
exclusion, and the DBH stumpage provenance added to the ledger notes.
"""

from __future__ import annotations

import pytest

from scripts import extract_meris_qc_full as extract

PROFILE_ROWS = [
    {"NoProfil": str(profile), "Description": description}
    for profile, description in extract.PROFILE_DESCRIPTIONS.items()
]


def price_source(**overrides: str) -> dict[str, str]:
    row = {
        "ESSENCE": "BOJ",
        "CategorieEssence": "Bop",
        "TypeEssence": "FT",
        "TypeEssenceEquat": "FT",
    }
    for product in extract.PRODUCTS:
        for code, _ in extract.SERIES:
            column = f"Prix_{extract.PRICE_COLUMNS[product]}_{code}"
            inclusion = f"Redev_{extract.PRICE_COLUMNS[product]}_{code}_Inclus"
            row[column] = "0.0000"
            row[inclusion] = "0"
    row.update(overrides)
    return row


def curve_source(
    *,
    species: str = "BOJ",
    series: str = "courant",
    dbh: str = "10",
    revenue: str = "100.0000",
    included: str = "1",
) -> dict[str, str]:
    return {
        "ESSENCE": species,
        "Type": series,
        "DHP": dbh,
        "SCIAGE": revenue,
        "PATE": revenue,
        "ORIGINE": "sep",
        "InclureRedev": included,
    }


def build_profiles(
    profile_2: list[dict[str, str]] | None = None,
    profile_3: list[dict[str, str]] | None = None,
    profile_4: list[dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    return extract.build_curves(
        {
            2: profile_2 or [curve_source()],
            3: profile_3 or [curve_source()],
            4: profile_4 or [curve_source()],
        },
        PROFILE_ROWS,
    )


def placeholder_curve() -> dict[str, object]:
    return {
        "profile": extract.CURVE_PROFILE,
        "species": "BOJ",
        "type": "courant",
        "dbh": "10",
        "revenue": "0.0100",
        "stumpage_included": "1",
    }


def test_price_deck_labels_zero_placeholder_and_positive_cells() -> None:
    row = price_source(
        Prix_Der_2="0.0000",
        Prix_F1_2="202.9370",
        Redev_F1_2_Inclus="1",
        Prix_F3_2="0.0100000005",
        Prix_Pate_2="0.0100",
    )

    long_rows, wide_rows = extract.build_price_deck([row])
    status = {
        (item["product"], item["series"]): item["status"] for item in long_rows
    }

    assert status[("DER", "courant")] == "not_applicable_zero"
    assert status[("F1", "courant")] == "model_input"
    assert status[("F3", "courant")] == "placeholder_0.0100"
    assert status[("PATE", "courant")] == "placeholder_0.0100"
    f1 = next(
        item
        for item in long_rows
        if item["product"] == "F1" and item["series"] == "courant"
    )
    assert f1["stumpage_included"] == "1"
    assert wide_rows[0]["Redev_F1_2_Inclus"] == "1"


def test_price_deck_rejects_unknown_stumpage_flag() -> None:
    row = price_source(Redev_F1_2_Inclus="")
    with pytest.raises(SystemExit, match="stumpage-inclusion flag"):
        extract.build_price_deck([row])


def test_curves_keep_revenue_and_stumpage_provenance() -> None:
    curves = build_profiles()

    assert len(curves) == 3
    assert {row["source"] for row in curves} == {
        "tbl_essences_revenus_EssDhp_Profil_2:SCIAGE+PATE",
        "tbl_essences_revenus_EssDhp_Profil_3:SCIAGE+PATE",
        "tbl_essences_revenus_EssDhp_Profil_4:SCIAGE+PATE",
    }
    assert {row["stumpage_included"] for row in curves} == {"1"}


def test_curves_reject_unknown_series_type() -> None:
    with pytest.raises(SystemExit, match="unexpected MERIS revenue series"):
        build_profiles(profile_2=[curve_source(series="Courant")])


def test_curves_reject_unknown_stumpage_flag() -> None:
    with pytest.raises(SystemExit, match="stumpage-inclusion flag"):
        build_profiles(profile_2=[curve_source(included="2")])


def test_curves_reject_sciage_pate_divergence() -> None:
    row = curve_source()
    row["PATE"] = "99.0000"
    with pytest.raises(SystemExit, match="SCIAGE\\+PATE|SEPM revenue columns differ"):
        build_profiles(profile_2=[row])


def test_profile_equivalence_accepts_identical_profiles() -> None:
    canonical = extract.canonical_curve_profile(build_profiles())
    assert len(canonical) == 1


def test_profile_equivalence_rejects_revenue_divergence() -> None:
    curves = build_profiles()
    row = next(item for item in curves if item["profile"] == "Profil_3")
    row["revenue"] = "101.0000"
    with pytest.raises(SystemExit, match="differs from Profil_2"):
        extract.canonical_curve_profile(curves)


def test_profile_equivalence_rejects_stumpage_divergence() -> None:
    curves = build_profiles()
    row = next(item for item in curves if item["profile"] == "Profil_4")
    row["stumpage_included"] = "0"
    with pytest.raises(SystemExit, match="differs from Profil_2"):
        extract.canonical_curve_profile(curves)


def test_profile_equivalence_requires_canonical_profile() -> None:
    curves = extract.build_curves(
        {3: [curve_source()], 4: [curve_source()]},
        PROFILE_ROWS,
    )
    with pytest.raises(SystemExit, match="no Profil_2"):
        extract.canonical_curve_profile(curves)


def test_ledger_maps_quality_classes_and_series_suffix() -> None:
    row = price_source(
        Prix_Der_2="50.0000",
        Redev_Der_2_Inclus="1",
        Prix_F1_2="202.9370",
        Redev_F1_2_Inclus="1",
        Prix_Pate_2="40.0000",
        Redev_Pate_2_Inclus="0",
        Prix_Autre_2="75.0000",
        Redev_Autre_2_Inclus="1",
    )

    ledger = extract.build_ledger_rows([row], [placeholder_curve()], [], [])
    by_quality = {item["quality"]: item for item in ledger}

    assert set(by_quality) == {"DER", "F1", "PATE", "AUTRE"}
    assert by_quality["DER"]["product"] == "peeler"
    assert by_quality["F1"]["product"] == "sawlog"
    assert by_quality["PATE"]["product"] == "pulpwood"
    assert by_quality["AUTRE"]["product"] == "mixed_log"
    assert by_quality["AUTRE"]["confidence"] == "low"
    assert by_quality["PATE"]["confidence"] == "medium"
    assert all(item["licence"].endswith("-C") for item in ledger)
    assert all("series=courant" in item["notes"] for item in ledger)
    assert all("series_non_additive=1" in item["notes"] for item in ledger)
    assert "stumpage_included=0" in by_quality["PATE"]["notes"]


def test_ledger_excludes_price_placeholders_with_float_tolerance() -> None:
    row = price_source(
        Prix_F1_2="0.0100000005",
        Prix_F3_2="0.0100",
        Prix_Pate_2="0.0000",
    )

    assert (
        extract.build_ledger_rows([row], [placeholder_curve()], [], []) == []
    )


def test_ledger_writes_dbh_curve_with_stumpage_provenance() -> None:
    profile = [
        curve_source(series="courant"),
        curve_source(series="tendance"),
    ]
    curves = build_profiles(profile, profile, profile)
    ledger = extract.build_ledger_rows([], curves, [], [])

    assert len(ledger) == 2
    assert {row["dbh_cm"] for row in ledger} == {"10"}
    assert {row["licence"] for row in ledger} == {
        "QC-MERIS-CURVE-BOJ-10-C",
        "QC-MERIS-CURVE-BOJ-10-T",
    }
    for row in ledger:
        assert row["quality"] == "NA"
        assert row["product"] == "mixed_log"
        assert "stumpage_included=1" in row["notes"]
        assert "series_non_additive=1" in row["notes"]
        assert "profile_equivalence=Profil_2=Profil_3=Profil_4" in row["notes"]


def test_ledger_excludes_curve_placeholders() -> None:
    curves = build_profiles(
        profile_2=[curve_source(revenue="0.0100000005")],
        profile_3=[curve_source(revenue="0.0100000005")],
        profile_4=[curve_source(revenue="0.0100000005")],
    )

    assert extract.build_ledger_rows([], curves, [], []) == []


def test_ledger_rejects_unknown_series_on_a_curve_row() -> None:
    curve = {
        "profile": extract.CURVE_PROFILE,
        "species": "BOJ",
        "type": "Courant",
        "dbh": "10",
        "revenue": "100.0000",
        "stumpage_included": "1",
    }
    with pytest.raises(SystemExit, match="unexpected MERIS revenue series"):
        extract.build_ledger_rows([], [curve], [], [])


def test_ledger_rejects_unknown_stumpage_flag_on_a_curve_row() -> None:
    curve = {
        "profile": extract.CURVE_PROFILE,
        "species": "BOJ",
        "type": "courant",
        "dbh": "10",
        "revenue": "100.0000",
        "stumpage_included": "",
    }
    with pytest.raises(SystemExit, match="stumpage-inclusion flag"):
        extract.build_ledger_rows([], [curve], [], [])
