"""Tests for the BC VRI ingestion path used by ``run_bc_supply``.

These tests cover:

- the BC VRI ``process_bc_vri`` reader path on the local stub fixture,
- the CASFRI-shape row contract (30 + 3 wrapper columns),
- the ``run_bc_supply`` thin wrapper that bridges CASFRI-shape -> ws3
  supply-offer rows for the LP,
- the pure helpers (``species_resolve``, ``make_cas_id``, ``safe_float``)
  that any reader implementation must respect.

The tests deliberately use the stub BC VRI archive at
``tests/fixtures/bc_vri_sample/`` (3 polygons) rather than a real
DataBC archive, so CI stays hermetic. Production scale-ups would just
point ``run_bc_vri_supply`` at the real archive path.
"""

from __future__ import annotations

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from fresh_fibre.bc_qc_mini_2prov import (
    DEFAULT_BC_VRI_CSV_STUB,
    DEFAULT_BC_VRI_TO_WS3_MAPPING,
    run_bc_supply,
    run_bc_vri_supply,
)
from fresh_fibre.bc_vri_reader import (
    BC_OUTPUT_COLUMNS,
    DEFAULT_BC_FEATURE_CLASS,
    JURISDICTION,
    SOURCE_LABEL,
    BcLookupTables,
    make_cas_id,
    process_bc_vri,
    safe_float,
    safe_int,
    species_resolve,
    translate_row_to_casfri,
)
from fresh_fibre.ws3_export import SUPPLY_OFFER_COLUMNS


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestRunBcVriSupplySmokeCsv(unittest.TestCase):
    """`run_bc_vri_supply` writes a 3-row CSV against the unit-test stub."""

    def test_process_bc_vri_stub_emits_three_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            summary = process_bc_vri(
                gdb_path=DEFAULT_BC_VRI_CSV_STUB,
                lookup_dir=Path("/nonexistent"),
                out_dir=out_dir,
                csv_fallback_path=DEFAULT_BC_VRI_CSV_STUB,
            )
            self.assertEqual(summary["raw_polygon_count"], 3)
            written_paths = [Path(p) for p in summary["output_paths"]]
            self.assertEqual(len(written_paths), 1)
            rows = read_csv(written_paths[0])
            # Header row + 3 polygons.
            self.assertEqual(len(rows), 3)
            poly_ids = {row["poly_id"] for row in rows}
            self.assertEqual(poly_ids, {"100001", "100002", "100003"})

    def test_run_bc_vri_supply_dispatch_matches_brief_signature(self) -> None:
        """`run_bc_vri_supply` returns the documented summary dict."""
        with tempfile.TemporaryDirectory() as tmp:
            out_csv = Path(tmp) / "supply_offer_bc_vri.csv"
            summary = run_bc_vri_supply(
                gdb_path=DEFAULT_BC_VRI_CSV_STUB,
                out_csv=out_csv,
                csv_fallback_path=DEFAULT_BC_VRI_CSV_STUB,
            )
            self.assertIn("raw_polygon_count", summary)
            self.assertEqual(summary["raw_polygon_count"], 3)
            self.assertTrue(out_csv.exists())
            rows = read_csv(out_csv)
            self.assertEqual(len(rows), 3)


class TestTranslateRowToCasfriSchema(unittest.TestCase):
    """`translate_row_to_casfri` produces a 30 + 3 wrapper column row."""

    def test_translate_row_to_casfri_returns_30_columns(self) -> None:
        """Map one stub row, assert all 30 CASFRI columns present."""
        stub_rows = read_csv(DEFAULT_BC_VRI_CSV_STUB)
        self.assertGreater(len(stub_rows), 0)
        translated = translate_row_to_casfri(stub_rows[0], raw_objectid="1")
        expected = set(BC_OUTPUT_COLUMNS)
        self.assertEqual(set(translated.keys()), expected)
        self.assertEqual(len(BC_OUTPUT_COLUMNS), 33)

    def test_translate_row_jurisdiction_and_source_are_constant(self) -> None:
        stub_rows = read_csv(DEFAULT_BC_VRI_CSV_STUB)
        for row in stub_rows:
            translated = translate_row_to_casfri(row)
            self.assertEqual(translated["jurisdiction"], JURISDICTION)
            self.assertEqual(translated["source"], SOURCE_LABEL)
            self.assertEqual(translated["source_raw_table"], DEFAULT_BC_FEATURE_CLASS)


class TestRunBcSupplyBridge(unittest.TestCase):
    """`run_bc_supply` replaces the SCANFI synthetic BC path with BC VRI."""

    def test_bc_vri_supply_replaces_scanfi_synthetic_in_bc_qc_mini(self) -> None:
        """`run_bc_supply` writes ws3-shape rows that match the contract."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result = run_bc_supply(
                gdb_path=DEFAULT_BC_VRI_CSV_STUB,
                bc_mini_dir=Path("/tmp/bc-mini-ignore"),  # bc_mini ignored now
                output_dir=out_dir,
            )
            supply_offer_csv = result["supply_offer_path"]
            self.assertTrue(supply_offer_csv.exists())
            rows = read_csv(supply_offer_csv)
            # Test fixture stub maps each of 3 polygons to 1 ws3 row => 3 rows.
            self.assertEqual(len(rows), 3)
            # Output schema matches the LP-facing ws3 supply-offer contract.
            self.assertEqual(set(rows[0].keys()), set(SUPPLY_OFFER_COLUMNS))
            # CASFRI-shape path is also emitted for downstream consumers.
            casfri_csv = result["casfri_output_path"]
            self.assertTrue(casfri_csv.exists())
            casfri_rows = read_csv(casfri_csv)
            self.assertEqual(len(casfri_rows), 3)
            # Bridge provenance rows.
            self.assertEqual(result["scenario_id"], "bc_mini_001")
            self.assertEqual(result["ws3_run_id"], "bc_vri_diagnostic_supply_001")
            self.assertEqual(result["source_model"], SOURCE_LABEL)
            self.assertEqual(result["raw_polygon_count"], 3)


class TestSpeciesResolutionDropsEmpty(unittest.TestCase):
    """`species_resolve` filters None / empty ``SPECIES_CD_*`` slots."""

    def test_species_resolution_drops_empty_codes(self) -> None:
        """Empty ``species_cd_*`` slots drop out of the resolved pairs."""
        row = {
            "SPECIES_CD_1": "SAB",
            "SPECIES_PCT_1": "90",
            "SPECIES_CD_2": "",  # empty -> skipped
            "SPECIES_PCT_2": "5",
            "SPECIES_CD_3": None,  # None -> skipped
            "SPECIES_PCT_3": "5",
            "SPECIES_CD_4": "AT",
            "SPECIES_PCT_4": "0",  # zero-pct -> filtered downstream
            "SPECIES_CD_5": "  ",  # whitespace-only -> skipped
            "SPECIES_PCT_5": "1",
        }
        pairs = species_resolve(row)
        self.assertEqual(pairs, [("SAB", 90.0), ("AT", 0.0)])
        # Empty codes never appear in the pair list - explicit type assertion.
        for code, _ in pairs:
            self.assertTrue(code.strip())

    def test_species_resolution_all_empty_returns_empty_list(self) -> None:
        row = {key: "" for key in (
            "SPECIES_CD_1", "SPECIES_CD_2", "SPECIES_CD_3",
            "SPECIES_CD_4", "SPECIES_CD_5", "SPECIES_CD_6",
        )}
        self.assertEqual(species_resolve(row), [])


class TestMakeCasIdPrefix(unittest.TestCase):
    """`make_cas_id` returns ``BC_VRI_<poly>_<map>`` shaped ids."""

    def test_make_cas_id_uses_bc_prefix(self) -> None:
        cas_id = make_cas_id(poly_id="12345", map_id="092J07")
        self.assertTrue(cas_id.startswith("BC_VRI_"))
        # Both poly_id and map_id land inside the id; map_id first by argument.
        self.assertIn("092J07", cas_id)
        self.assertIn("12345", cas_id)


class TestSafeParsingHeavyNulls(unittest.TestCase):
    """`safe_float` / `safe_int` keep excepted fields as None under heavy nulls."""

    def test_safe_parsing_handles_heavy_nulls(self) -> None:
        """Feeds in rows with all-None numerics; excepted fields stay None."""
        row: dict[str, object] = {
            "CROWN_CLOSURE_PCT": None,
            "BASAL_AREA_M2_HA": "",
            "STEMS_HA": "  ",
            "HEIGHT_M": "not_a_number",
            "AGE_YR": "",
            "SITE_INDEX_M": None,
            "SHAPE_AREA": "",
        }
        self.assertIsNone(safe_float(row["CROWN_CLOSURE_PCT"]))
        self.assertIsNone(safe_float(row["BASAL_AREA_M2_HA"]))
        self.assertIsNone(safe_float(row["STEMS_HA"]))
        self.assertIsNone(safe_float(row["HEIGHT_M"]))
        self.assertIsNone(safe_int(row["AGE_YR"]))
        self.assertIsNone(safe_float(row["SITE_INDEX_M"]))
        # The excepted field (CROWN_CLOSURE_PCT even when blank) lands as None.
        translated = translate_row_to_casfri(row)
        self.assertIsNone(translated["crown_closure_pct"])
        self.assertIsNone(translated["basal_area_m2_ha"])
        self.assertIsNone(translated["stems_ha"])
        self.assertIsNone(translated["height_m"])
        self.assertIsNone(translated["age_yr"])
        self.assertIsNone(translated["site_index_m"])

    def test_safe_parsing_positive_numbers_round_trip(self) -> None:
        """Sanity: legitimate numeric strings parse normally."""
        self.assertEqual(safe_float("38.5"), 38.5)
        self.assertEqual(safe_int("140"), 140)
        self.assertEqual(safe_float("0.0"), 0.0)


class TestBcLookupTablesTolerant(unittest.TestCase):
    """`BcLookupTables.from_csv_dir` is tolerant of missing files."""

    def test_missing_lookup_dir_returns_empty_index(self) -> None:
        tables = BcLookupTables.from_csv_dir(Path("/nonexistent"))
        self.assertEqual(tables.species_by_source_val, {})


if __name__ == "__main__":
    unittest.main()
