"""Regression tests for the national residue supply-curve sensitivity v1."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import national_residue_curve as curve

ARTIFACT_DIR = curve.OUTPUT_DIR
NATIONAL_CSV = ARTIFACT_DIR / "national_residue_curve.csv"
PROVINCE_CSV = ARTIFACT_DIR / "province_residue_curve.csv"
OSECAN_CSV = ARTIFACT_DIR / "osecan_demand_context.csv"
SUMMARY_JSON = ARTIFACT_DIR / "summary.json"
MANIFEST_JSON = ARTIFACT_DIR / "manifest.json"
REPORT_MD = ARTIFACT_DIR / "NATIONAL_RESIDUE_CURVE_V1.md"
FACILITY_CSV = curve.FACILITY_DIR / "facility_residue_supply.csv"
FACILITY_PROVINCE_CSV = curve.FACILITY_DIR / "province_residue_supply.csv"
PRODUCT_CHAIN_CSV = curve.PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"

NUMERIC_COLUMNS = [
    "availability_fraction",
    "base_residue_odt",
    "residue_odt",
    "residue_pj",
    "lhv_gj_per_odt",
    "rsyc_base_119m_recoverable_odt",
    "rsyc_base_119m_recoverable_pj",
    "rsyc_ratio_to_119m_base",
]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("national_residue_curve")
    summary = curve.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def national_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / "national_residue_curve.csv"), NUMERIC_COLUMNS)


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / "province_residue_curve.csv"), NUMERIC_COLUMNS)


def _label(value) -> str:
    return str(value).strip().lower()


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    for name in (
        "national_residue_curve.csv",
        "province_residue_curve.csv",
        "osecan_demand_context.csv",
        "summary.json",
        "NATIONAL_RESIDUE_CURVE_V1.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
        assert (target / name).stat().st_size > 0, name
    assert summary["model_estimate"] is True
    assert summary["observed"] is False
    assert summary["policy_grade"] is False
    assert summary["not_policy"] is True
    assert summary["ui_or_public_osecan_changed"] is False


def test_scenario_arithmetic(national_rows, province_rows, built):
    summary, _ = built
    frames = (national_rows, province_rows)
    for frame in frames:
        for _, row in frame.iterrows():
            expected = float(row["base_residue_odt"]) * float(
                row["availability_fraction"]
            )
            assert float(row["residue_odt"]) == pytest.approx(
                expected, rel=1e-9, abs=1e-6
            )
    zero = national_rows[
        national_rows["availability_case"] == "process_availability_000"
    ]
    assert (zero["residue_odt"] == 0.0).all()
    assert (zero["residue_pj"] == 0.0).all()

    process_cases = {
        "process_availability_000": 0.0,
        "process_availability_025": 0.25,
        "process_availability_050": 0.50,
        "process_availability_075": 0.75,
    }
    harvest_cases = {
        "harvest_recovery_035": 0.35,
        "harvest_recovery_060": 0.60,
        "harvest_recovery_075": 0.75,
    }
    for frame in frames:
        for case, fraction in {**process_cases, **harvest_cases}.items():
            selected = frame[frame["availability_case"] == case]
            assert not selected.empty
            assert (selected["availability_fraction"] == fraction).all()
        for residue_class in (*curve.CLASS_ORDER, curve.TOTAL_CLASS):
            selected = national_rows[
                (national_rows["residue_type"] == curve.PROCESS_TYPE)
                & (national_rows["residue_class"] == residue_class)
            ]
            ordered = selected.sort_values("availability_fraction")
            values = ordered["residue_odt"].tolist()
            assert values == sorted(values)
            assert values[0] == 0.0
    harvest = national_rows[national_rows["residue_type"] == curve.HARVEST_TYPE]
    ordered = harvest.sort_values("availability_fraction")
    assert ordered["residue_odt"].tolist() == sorted(ordered["residue_odt"].tolist())
    for _, row in harvest.iterrows():
        expected = float(row["base_residue_odt"]) * float(
            row["availability_fraction"]
        )
        assert float(row["residue_odt"]) == pytest.approx(
            expected, rel=1e-9, abs=1e-6
        )
    assert summary["scenario_totals"]["process"]["process_availability_050"][
        "residue_pj"
    ] == pytest.approx(
        summary["process_base_national"]["residue_pj"] * 0.50, rel=1e-9
    )


def test_process_class_and_total_conservation(national_rows, province_rows):
    for province in curve.PROVINCES:
        for case in (
            "process_availability_000",
            "process_availability_025",
            "process_availability_050",
            "process_availability_075",
        ):
            class_rows = province_rows[
                (province_rows["province"] == province)
                & (province_rows["availability_case"] == case)
                & (province_rows["residue_class"].isin(curve.CLASS_ORDER))
            ]
            total_rows = province_rows[
                (province_rows["province"] == province)
                & (province_rows["availability_case"] == case)
                & (province_rows["residue_class"] == curve.TOTAL_CLASS)
            ]
            assert len(class_rows) == len(curve.CLASS_ORDER)
            assert len(total_rows) == 1
            assert float(total_rows.iloc[0]["residue_odt"]) == pytest.approx(
                math.fsum(float(value) for value in class_rows["residue_odt"]),
                rel=1e-9,
                abs=1e-6,
            )
    for case in (
        "process_availability_000",
        "process_availability_025",
        "process_availability_050",
        "process_availability_075",
    ):
        for residue_class in (*curve.CLASS_ORDER, curve.TOTAL_CLASS):
            national = national_rows[
                (national_rows["availability_case"] == case)
                & (national_rows["residue_class"] == residue_class)
            ]
            province = province_rows[
                (province_rows["availability_case"] == case)
                & (province_rows["residue_class"] == residue_class)
            ]
            assert len(national) == 1
            assert float(national.iloc[0]["residue_odt"]) == pytest.approx(
                math.fsum(float(value) for value in province["residue_odt"]),
                rel=1e-9,
                abs=1e-6,
            )

    facility = _numeric(_read_csv(FACILITY_CSV), ["residue_odt"])
    facility["province"] = facility["province"].str.upper()
    facility["facility_class"] = facility["facility_class"].str.upper()
    base = national_rows[
        (national_rows["availability_case"] == "process_availability_075")
        & (national_rows["residue_type"] == curve.PROCESS_TYPE)
    ]
    for facility_class in curve.CLASS_ORDER:
        expected = math.fsum(
            float(value)
            for value in facility.loc[
                facility["facility_class"] == facility_class, "residue_odt"
            ]
        )
        row = base[base["residue_class"] == facility_class]
        assert len(row) == 1
        assert float(row.iloc[0]["base_residue_odt"]) == pytest.approx(
            expected, rel=1e-9, abs=1e-6
        )
    total = base[base["residue_class"] == curve.TOTAL_CLASS]
    assert float(total.iloc[0]["base_residue_odt"]) == pytest.approx(
        math.fsum(float(value) for value in facility["residue_odt"]),
        rel=1e-9,
        abs=1e-6,
    )


def test_harvest_gross_and_recovery(national_rows, province_rows, built):
    summary, _ = built
    source = _numeric(_read_csv(FACILITY_PROVINCE_CSV), ["residue_odt"])
    source = source[source["residue_type"] == "harvest_residue_gross"].set_index(
        "province"
    )
    for province in curve.PROVINCES:
        rows = province_rows[
            (province_rows["province"] == province)
            & (province_rows["residue_type"] == curve.HARVEST_TYPE)
        ]
        assert len(rows) == len(curve.HARVEST_RECOVERY_CASES)
        gross = float(source.loc[province, "residue_odt"])
        assert all(
            float(value) == pytest.approx(gross, rel=1e-9, abs=1e-6)
            for value in rows["base_residue_odt"]
        )
        for _, row in rows.iterrows():
            assert float(row["residue_odt"]) == pytest.approx(
                gross * float(row["availability_fraction"]),
                rel=1e-9,
                abs=1e-6,
            )
    national_gross = math.fsum(float(value) for value in source["residue_odt"])
    harvest = national_rows[national_rows["residue_type"] == curve.HARVEST_TYPE]
    assert len(harvest) == len(curve.HARVEST_RECOVERY_CASES)
    assert all(
        float(value) == pytest.approx(national_gross, rel=1e-9, abs=1e-6)
        for value in harvest["base_residue_odt"]
    )
    assert summary["harvest_base_national"]["gross_odt"] == pytest.approx(
        national_gross, rel=1e-9
    )
    assert summary["harvest_base_national"]["base_recoverable_0p60_odt"] == pytest.approx(
        national_gross * 0.60, rel=1e-9
    )


def test_pj_conversion(national_rows, province_rows, built):
    summary, _ = built
    lhv = 18.5
    for frame in (national_rows, province_rows):
        for _, row in frame.iterrows():
            assert float(row["lhv_gj_per_odt"]) == pytest.approx(lhv)
            assert float(row["residue_pj"]) == pytest.approx(
                float(row["residue_odt"]) * lhv / curve.PJ_GJ,
                rel=1e-9,
                abs=1e-9,
            )
    for family in ("process", "harvest"):
        for values in summary["scenario_totals"][family].values():
            odt_key = "residue_odt" if family == "process" else "recoverable_odt"
            pj_key = "residue_pj" if family == "process" else "recoverable_pj"
            assert values[pj_key] == pytest.approx(
                values[odt_key] * lhv / curve.PJ_GJ, rel=1e-9
            )


def test_rsyc_comparison(national_rows, built):
    summary, _ = built
    harvest = national_rows[national_rows["residue_type"] == curve.HARVEST_TYPE]
    assert len(harvest) == 3
    ratios = {
        row["availability_case"]: float(row["rsyc_ratio_to_119m_base"])
        for _, row in harvest.iterrows()
    }
    assert ratios["harvest_recovery_035"] == pytest.approx(
        3_174_188.095614494 / 7_080_062.0860263305, rel=1e-9
    )
    assert ratios["harvest_recovery_060"] == pytest.approx(
        0.768561806470979, rel=1e-9
    )
    assert ratios["harvest_recovery_075"] == pytest.approx(
        6_801_831.63345963 / 7_080_062.0860263305, rel=1e-9
    )
    assert summary["rsyc_comparison"]["base_119m_recoverable_odt"] == pytest.approx(
        7_080_062.0860263305, rel=1e-9
    )
    assert summary["rsyc_comparison"]["base_119m_recoverable_pj"] == pytest.approx(
        130.9811485914871, rel=1e-9
    )
    assert summary["rsyc_comparison"]["base_100m_recoverable_pj"] == pytest.approx(
        129.6709066255944, rel=1e-9
    )
    assert summary["rsyc_comparison"]["rsyc_comparable_stream"] == curve.HARVEST_TYPE


def test_rsyc_comparable_only_for_harvest(national_rows, province_rows):
    for frame in (national_rows, province_rows):
        harvest = frame[frame["residue_type"] == curve.HARVEST_TYPE]
        process = frame[frame["residue_type"] == curve.PROCESS_TYPE]
        assert not harvest.empty
        assert not process.empty
        assert (harvest["rsyc_comparable"] == "true").all()
        assert (process["rsyc_comparable"] == "false").all()
        assert (
            harvest["rsyc_base_119m_recoverable_odt"] > 0.0
        ).all()
        assert (process["rsyc_base_119m_recoverable_odt"] == 0.0).all()
        assert (process["rsyc_ratio_to_119m_base"] == 0.0).all()


def test_no_double_counting_and_unique_keys(national_rows, province_rows):
    for frame in (national_rows, province_rows):
        keys = list(
            zip(
                frame["geography_level"],
                frame["province"],
                frame["residue_type"],
                frame["residue_class"],
                frame["availability_case"],
            )
        )
        assert len(keys) == len(set(keys))
        assert set(frame["residue_type"]) == {
            curve.PROCESS_TYPE,
            curve.HARVEST_TYPE,
        }
        assert not frame["residue_type"].str.contains("combined").any()
        assert not frame["residue_type"].str.contains("gross").any()
        assert (frame["in_supply_curve"] == "true").all()
    process_cases = {case for _, case in curve.PROCESS_AVAILABILITY_CASES}
    harvest_cases = {case for _, case in curve.HARVEST_RECOVERY_CASES}
    for province in curve.PROVINCES:
        process = province_rows[
            (province_rows["province"] == province)
            & (province_rows["residue_type"] == curve.PROCESS_TYPE)
            & (province_rows["availability_case"] == "process_availability_050")
        ]
        assert set(process["availability_case"]) == {"process_availability_050"}
        assert len(process) == len(curve.CLASS_ORDER) + 1
        harvest = province_rows[
            (province_rows["province"] == province)
            & (province_rows["residue_type"] == curve.HARVEST_TYPE)
        ]
        assert set(harvest["availability_case"]) == harvest_cases
    assert process_cases == {
        "process_availability_000",
        "process_availability_025",
        "process_availability_050",
        "process_availability_075",
    }
    # A selected pair of one process case and one harvest case is additive
    # only because the streams are separate; the rows themselves never merge.
    process_national = national_rows[
        (national_rows["residue_type"] == curve.PROCESS_TYPE)
        & (national_rows["residue_class"] == curve.TOTAL_CLASS)
        & (national_rows["availability_case"] == "process_availability_050")
    ]
    harvest_national = national_rows[
        (national_rows["residue_type"] == curve.HARVEST_TYPE)
        & (national_rows["availability_case"] == "harvest_recovery_060")
    ]
    assert len(process_national) == 1
    assert len(harvest_national) == 1
    assert float(process_national.iloc[0]["residue_pj"]) > 0.0
    assert float(harvest_national.iloc[0]["residue_pj"]) > 0.0


def test_labels_and_report(built):
    summary, target = built
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["not_policy"] is True
    assert summary["labels"]["rsyc_comparable"] == "harvest_recoverable_only"
    for frame in (
        _read_csv(target / "national_residue_curve.csv"),
        _read_csv(target / "province_residue_curve.csv"),
    ):
        assert all(_label(value) == "true" for value in frame["model_estimate"])
        assert all(_label(value) == "false" for value in frame["observed"])
        assert all(_label(value) == "false" for value in frame["policy_grade"])
        assert all(_label(value) == "true" for value in frame["not_policy"])
        assert set(frame["evidence_tier"]).issubset(
            {
                "facility_process_residue_v1:bc_anchor_species_group",
                "facility_process_residue_v1:donor_coefficient",
                "facility_process_residue_v1:bc_anchor_species_group+donor_coefficient",
                (
                    "facility_process_residue_v1:national_aggregate:"
                    "bc_anchor_species_group+donor_coefficient"
                ),
                "facility_process_residue_v1:no_positive_residue",
                "rsyc_province_factor:bc_anchor_species_group",
                "rsyc_province_factor:donor_coefficient",
                "rsyc_province_factor:national_province_factor_aggregate",
            }
        )
    text = (target / "NATIONAL_RESIDUE_CURVE_V1.md").read_text(encoding="utf-8")
    for phrase in (
        "National Residue Supply-Curve Sensitivity v1",
        "## Scenario definitions",
        "## National process residue (PJ)",
        "## National harvest residue (gross and recoverable)",
        "## Province process residue (PJ)",
        "## Province harvest residue (recoverable PJ)",
        "## RSYC and OSeCAN comparison",
        "## Caveats",
        "## Tests and status",
        "No policy/AAC claim is made",
        "observed=false",
        "policy_grade=false",
        "not_policy=true",
        "129.6709",
        "152.923",
        "43.524",
    ):
        assert phrase in text, phrase


def test_summary_manifest_hashes(built):
    summary, target = built
    saved = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    assert saved["artifact"] == curve.ARTIFACT
    assert saved["status"] == "pass"
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact"] == curve.ARTIFACT
    assert manifest["status"] == "pass"
    assert manifest["labels"]["model_estimate"] is True
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["not_policy"] is True
    assert manifest["labels"]["ui_or_public_osecan_changed"] is False
    for name, metadata in manifest["outputs"].items():
        path = target / name
        assert path.is_file(), name
        assert path.stat().st_size == metadata["bytes"], name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"], name
    assert summary["outputs"] == manifest["outputs"]


def test_osecan_context_and_inputs_unchanged(built):
    summary, target = built
    context = summary["osecan_context"]
    assert context["run"] == "stress_119m"
    assert context["source"] == str(
        curve.OSECAN_DISPATCH.relative_to(curve.REPO_ROOT)
    )
    assert context["y2050"]["national_supply_pj"] == pytest.approx(
        152.92277, abs=1e-5
    )
    assert context["y2050"]["bc_supply_pj"] == pytest.approx(43.52377, abs=1e-5)
    assert context["y2050"]["national_power_dispatch_pj"] == pytest.approx(
        41.108271, abs=1e-6
    )
    assert context["y2050"]["bc_power_dispatch_pj"] == pytest.approx(
        11.699938, abs=1e-6
    )
    source_hash = hashlib.sha256(curve.OSECAN_DISPATCH.read_bytes()).hexdigest()
    assert context["source_sha256"] == source_hash
    assert (
        summary["inputs"][str(curve.OSECAN_DISPATCH.relative_to(curve.REPO_ROOT))]
        == source_hash
    )
    demand = _numeric(
        _read_csv(target / "osecan_demand_context.csv"),
        [
            "year",
            "national_supply_pj",
            "bc_supply_pj",
            "national_power_dispatch_pj",
            "bc_power_dispatch_pj",
        ],
    )
    assert demand["year"].tolist() == list(range(2021, 2071))
    assert set(demand["run"]) == {"stress_119m"}
    assert (
        demand["bc_supply_pj"] <= demand["national_supply_pj"] + 1e-6
    ).all()
    assert (
        demand["national_power_dispatch_pj"]
        <= demand["national_supply_pj"] + 1e-6
    ).all()


def test_no_unexpected_deletions_gate():
    curve.assert_no_unexpected_deletions()


def test_committed_artifact_is_internally_consistent():
    if not SUMMARY_JSON.is_file():
        pytest.skip("committed artifact is not materialized in this checkout")
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    assert summary["artifact"] == curve.ARTIFACT
    assert summary["status"] == "pass"
    assert summary["validation"]["failed_check_count"] == 0
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    for name, metadata in manifest["outputs"].items():
        path = ARTIFACT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == metadata["bytes"], name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"], name
    assert REPORT_MD.is_file()
