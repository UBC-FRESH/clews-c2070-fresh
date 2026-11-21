"""Parser tests for the BC public appraisal cost extractor."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/build_bc_appraisal_costs.py"
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/bc_appraisal_costs"


def load_module():
    spec = importlib.util.spec_from_file_location("build_bc_appraisal_costs", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load build_bc_appraisal_costs")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_source(module, key="silv_oct"):
    return module.Source(
        key=key,
        label="Interior Basic Silviculture 2026-10",
        filename="basic_silv_costs_oct_26.pdf",
        url="https://example.invalid/basic_silv_costs_oct_26.pdf",
        effective_date="2026-10-01",
        retrieved_utc="2026-11-21T20:46:00+00:00",
        sha256="0" * 64,
        pages=1,
    )


def test_parse_basic_silviculture_fixture():
    module = load_module()
    source = make_source(module)
    text = (FIXTURE_DIR / "silviculture_oct_26.txt").read_text(encoding="utf-8")
    rows: list[dict[str, object]] = []

    count = module.parse_basic_silviculture(text, source, rows)

    assert count == 7
    values = {row["zone"]: row["value"] for row in rows}
    assert values["North Central Zone"] == pytest.approx(10.02)
    assert values["North West Zone"] == pytest.approx(5.84)
    assert all(row["cost_basis"] == "CAD/m3" for row in rows)
    assert all(row["observation_type"] == "appraisal_allowance" for row in rows)


def test_parse_iam_fma_table():
    module = load_module()
    source = make_source(module, key="iam")
    source = module.Source(
        **{**source.__dict__, "label": "IAM 2026", "effective_date": "2026-07-01"}
    )
    table = [
        ["", "Region", "", "", "Constant", "", "", "Min ($/m3)", "", "", "Max ($/m3)"],
        ["Cariboo", None, None, "15.38", None, None, "7.63", None, None, "25.50"],
        ["Kootenay-Boundary", None, None, "17.48", None, None, "7.34", None, None, "27.16"],
        ["Northeast", None, None, "20.69", None, None, "12.86", None, None, "24.89"],
        ["Omineca", None, None, "17.70", None, None, "9.38", None, None, "20.70"],
        ["Skeena", None, None, "16.71", None, None, "4.66", None, None, "25.99"],
        ["Thompson-Okanagan", None, None, "16.05", None, None, "6.03", None, None, "24.12"],
    ]
    rows: list[dict[str, object]] = []

    count = module.parse_iam_fma([table], source, rows)

    assert count == 6
    skeena = next(row for row in rows if row["zone"] == "Skeena")
    assert skeena["value"] == pytest.approx(16.71)
    assert skeena["min_value"] == pytest.approx(4.66)
    assert skeena["max_value"] == pytest.approx(25.99)


def test_number_rejects_non_numeric():
    module = load_module()
    with pytest.raises(module.ParseError):
        module.number("n/a")
