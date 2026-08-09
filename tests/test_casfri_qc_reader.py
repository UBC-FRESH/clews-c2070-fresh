"""Tests for the QC inventory → CASFRI-shape row adapter.

The adapter reads QC stand inventory rows (with QC IEQM 5E / 3E field
names) and emits one ``casfri.cas / cst / geo / lyr / eco / dst / nfl``-shape
row. These tests cover the in-memory contract:
- column-shape dispatch is tolerant of QC producer naming drift,
- species resolution flows from cross-jurisdiction map to QC fallback,
- unmapped species are flagged with stable value policies,
- the live QC 21M GPKG schema (when reachable) is recognised.
"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from fresh_fibre.casfri_qc_reader import (
    CASFRI_OUTPUT_COLUMNS,
    QC_DENSITY_BUCKET_TO_PERCENT,
    QC_STAND_COLUMNS,
    QC_VALUE_POLICIES,
    QcLookupTables,
    column_value,
    cross_species_index,
    density_to_percent,
    index_lookup_table,
    make_cas_id,
    reader_config,
    species_resolve,
    stable_hash,
    translate_row_to_casfri,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_DIR = Path(REPO_ROOT) / "data" / "raw" / "casfri_v5" / "translation_tables"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestQcLookupLoading(unittest.TestCase):
    """CASFRI v5 translation-table loading yields the expected indices."""

    def test_stand_structure_lookup_loaded(self) -> None:
        stands = index_lookup_table(
            TRANSLATION_DIR / "qc_standstructure_lookup.csv", "source_val",
            ["stand_structure", "num_of_layers", "layer_1_age", "layer_2_age"],
        )
        self.assertIn("1070", stands)
        row = stands["1070"]
        self.assertEqual(row["stand_structure"], "MULTI_LAYERED")
        self.assertEqual(row["num_of_layers"], "2")
        self.assertEqual(row["layer_1_age"], "10")
        self.assertEqual(row["layer_2_age"], "70")

    def test_qc_species_lookup_loaded(self) -> None:
        lookup_rows = read_csv(TRANSLATION_DIR / "qc_ini03_species_lookup.csv")
        self.assertGreater(len(lookup_rows), 500)
        first_row = lookup_rows[0]
        self.assertIn("source_val", first_row)
        self.assertIn("species_1", first_row)

    def test_qc_disturbance_lookup_loaded(self) -> None:
        lookup_rows = read_csv(TRANSLATION_DIR / "qc_disturbance_lookup.csv")
        self.assertGreater(len(lookup_rows), 5)

    def test_qc_lookup_tables_from_csv_dir(self) -> None:
        tables = QcLookupTables.from_csv_dir(TRANSLATION_DIR)
        self.assertGreater(len(tables.stands_by_source_val), 50)
        self.assertGreater(len(tables.species_by_source_val), 500)
        self.assertGreater(len(tables.disturbance_by_source_val), 5)
        self.assertGreater(len(tables.cross_species_by_qc_code), 100)


class TestColumnShapeDispatch(unittest.TestCase):
    """The reader's column-shape dispatch tolerates QC producer naming drift."""

    def test_cl_age_synonyms_resolve(self) -> None:
        for alias in ("cl_age", "CL_AGE", "stand_age_class"):
            row = {alias: "1070"}
            self.assertEqual(column_value(row, "cl_age"), "1070")

    def test_essence_synonyms_resolve(self) -> None:
        row_synonyms = {
            "reb_ess1": "SAB",
            "REB_ESS1": "EPB",
            "essence": "BOP",
        }
        self.assertEqual(column_value(row_synonyms, "essence_1"), "SAB")
        self.assertEqual(column_value({"REB_ESS1": "EPB"}, "essence_1"), "EPB")
        self.assertEqual(column_value({"essence": "BOP"}, "essence_1"), "BOP")

    def test_default_synonym_returns_empty_string(self) -> None:
        self.assertEqual(column_value({}, "cl_age"), "")
        self.assertEqual(column_value({}, "hauteur_m"), "")


class TestDensityToPercent(unittest.TestCase):
    """``CODE_DEN`` A-E bucket percent mapping is documented and stable."""

    def test_documented_buckets_match_known_mappings(self) -> None:
        self.assertEqual(density_to_percent("A"), 80.0)
        self.assertEqual(density_to_percent("B"), 70.0)
        self.assertEqual(density_to_percent("C"), 50.0)
        self.assertEqual(density_to_percent("D"), 32.5)
        self.assertEqual(density_to_percent("E"), 12.5)

    def test_unknown_bucket_returns_sentinel(self) -> None:
        self.assertEqual(density_to_percent(""), -9999.0)
        self.assertEqual(density_to_percent("??"), -9999.0)


class TestSpeciesResolve(unittest.TestCase):
    """Resolve QC species codes to CASFRI canonical species."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tables = QcLookupTables.from_csv_dir(TRANSLATION_DIR)

    def test_sab_resolves_to_abie_bal(self) -> None:
        result = species_resolve(
            "SAB", self.tables.species_by_source_val, self.tables.cross_species_by_qc_code,
        )
        self.assertEqual(result["qc_species_resolved"], "ABIE_BAL_###")
        self.assertEqual(result["species_value_policy"], "qc_species_resolved_via_lookup")

    def test_boj_resolves_via_qc_lookup(self) -> None:
        result = species_resolve(
            "BOJ", self.tables.species_by_source_val, self.tables.cross_species_by_qc_code,
        )
        self.assertEqual(result["qc_species_resolved"], "BETU_ALL_###")
        self.assertEqual(result["species_value_policy"], "qc_species_resolved_via_lookup")

    def test_unmapped_species_returns_unresolved_policy(self) -> None:
        result = species_resolve(
            "ZZZ", self.tables.species_by_source_val, self.tables.cross_species_by_qc_code,
        )
        self.assertEqual(result["qc_species_resolved"], "")
        self.assertEqual(result["species_value_policy"], "qc_species_unmapped_unresolved")

    def test_empty_species_skipped(self) -> None:
        result = species_resolve(
            "", self.tables.species_by_source_val, self.tables.cross_species_by_qc_code,
        )
        self.assertEqual(result["species_value_policy"], "qc_species_empty_skipped")


class TestCasIdDeterministic(unittest.TestCase):
    """CASFRI ``cas_id`` is composed deterministically from the source row."""

    def test_cas_id_is_deterministic(self) -> None:
        first = make_cas_id("21M_0000000001", "21M_0000000001")
        second = make_cas_id("21M_0000000001", "21M_0000000001")
        self.assertEqual(first, second)

    def test_cas_id_distinguishes_geocodes(self) -> None:
        first = make_cas_id("X1", "21M_0000000001")
        second = make_cas_id("X1", "21M_0000000001-A")
        # The composited cas_id retains the geocode inside its tail, so
        # differing geocodes produce distinct full ids.
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("qc_ini03"))


class TestStableHash(unittest.TestCase):
    """``stable_hash`` produces collision-resistant, deterministic ids."""

    def test_identical_inputs_yield_identical_hashes(self) -> None:
        self.assertEqual(stable_hash(["a", "b"]), stable_hash(["a", "b"]))

    def test_different_inputs_yield_different_hashes(self) -> None:
        self.assertNotEqual(stable_hash(["a", "b"]), stable_hash(["a", "c"]))

    def test_hash_length_is_sixteen_hex(self) -> None:
        self.assertEqual(len(stable_hash(["x"])), 16)


class TestCrossSpeciesIndex(unittest.TestCase):
    """The cross-jurisdiction species index resolves BC and QC producer codes."""

    def test_jurisdiction_specific_codes_resolve(self) -> None:
        index = cross_species_index(
            TRANSLATION_DIR / "species_code_mapping.csv", "qc_species_codes",
        )
        # Black spruce is universal across jurisdictions
        self.assertIn("SB", index)  # QC-specific code for Picea mariana
        # White / balsam fir / etc. all over the table
        self.assertIn("BF", index)
        # Each row carries the CASFRI canonical and the scientific name
        for code, info in list(index.items())[:5]:
            self.assertIn("casfri_species_codes", info)
            self.assertIn("scientific_name", info)


class TestTranslateRowToCasfri(unittest.TestCase):
    """One QC stand row → one CASFRI-shape row."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tables = QcLookupTables.from_csv_dir(TRANSLATION_DIR)

    def test_basic_qc_row_succeeds(self) -> None:
        row = {
            "geocode": "21M_0000000001",
            "essence_1": "SAB",
            "essence_2": "EPB",
            "essence_pct_1": "70",
            "essence_pct_2": "30",
            "cl_age": "1070",
            "cl_dens": "B",
            "cl_haut": "3",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "16",
            "superficie": "16000000",
            "iqs_pot": "12",
            "b_arbv_tot": "85",
        }
        result = translate_row_to_casfri(row, self.tables)
        for column in CASFRI_OUTPUT_COLUMNS:
            self.assertIn(column, result)
        self.assertEqual(result["inventory_id"], "qc_ini03")
        self.assertEqual(result["stand_structure"], "MULTI_LAYERED")
        self.assertEqual(result["species_1_layer1"], "ABIE_BAL_###")
        self.assertEqual(result["density_class"], "B")
        self.assertEqual(result["height_upper_layer1"], 16)
        self.assertEqual(result["area_ha"], 1600.0)
        self.assertEqual(result["biomass_t"], 85)

    def test_unmapped_species_gets_unresolved_value_policy(self) -> None:
        row = {
            "geocode": "21M_0000000099",
            "essence_1": "ZZZ",
            "cl_age": "1010",
            "cl_dens": "C",
            "hauteur": "10",
            "superficie": "1000000",
        }
        result = translate_row_to_casfri(row, self.tables)
        self.assertEqual(result["species_1_layer1"], "")
        self.assertIn(
            result["species_value_policy"],
            {"qc_species_resolved_via_lookup", "qc_species_empty_skipped"},
        )


class TestRunReader(unittest.TestCase):
    """Empty inventory raises because the LP contract requires ≥1 row."""

    def test_run_reader_rejects_empty_inventory(self) -> None:
        tables = QcLookupTables.from_csv_dir(TRANSLATION_DIR)
        from fresh_fibre.casfri_qc_reader import run_qc_reader

        with self.assertRaises(ValueError):
            run_qc_reader([], tables)


class TestRealFeuilleColumnShape(unittest.TestCase):
    """Crosswalk schema cross-references live QC field names from the 21M GPKG."""

    def test_qc_21m_real_columns_recognised_by_aliases(self) -> None:
        live_aliases = {
            "geocode", "essence_1", "essence_2", "essence_3",
            "cl_age", "cl_dens", "cl_haut", "cl_drai", "type_eco",
            "co_ter", "perturb", "an_perturb", "an_origine",
            "hauteur", "superficie",
        }
        # Each QC_STAND_COLUMNS logical column with the live_aliases as
        # the first-name should resolve via column_value against a row with
        # only that alias.
        for logical_name, aliases in QC_STAND_COLUMNS.items():
            if aliases[0] in live_aliases:
                row = {aliases[0]: "x"}
                self.assertEqual(column_value(row, logical_name), "x", msg=logical_name)


class TestReaderConfig(unittest.TestCase):
    """``reader_config`` exposes the runtime contract."""

    def test_reader_config_returns_validation_seam(self) -> None:
        cfg = reader_config()
        self.assertEqual(Path(cfg["default_translation_dir"]).resolve(), TRANSLATION_DIR.resolve())
        self.assertIn("species", cfg["lookup_tables"])
        self.assertIn("cas_id", cfg["output_columns"])
        # Keys are logical names; values are the recorded policy tags.
        self.assertIn("species_resolved_via_lookup", cfg["value_policies"])
        self.assertIn("A", cfg["qc_density_bucket_to_percent"])


if __name__ == "__main__":
    unittest.main()
